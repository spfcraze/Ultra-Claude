"""
Agentic Runner for Local LLM Support

Implements the agentic loop that allows local LLMs to use tools
for file operations, shell commands, and code editing.
"""
import asyncio
import json
import re
from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

from .llm_provider import (
    LLMProvider, LLMProviderConfig, LLMProviderType, LLMProviderStatus
)
from .tools import Tool, ToolResult, get_all_tools


@dataclass
class Message:
    """A message in the conversation"""
    role: str  # system, user, assistant, tool
    content: str
    tool_calls: List[Dict] = field(default_factory=list)
    tool_call_id: Optional[str] = None
    name: Optional[str] = None  # For tool messages

    def to_dict(self) -> Dict[str, Any]:
        """Convert to API format"""
        msg: Dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        if self.name:
            msg["name"] = self.name
        return msg


class AgenticRunner:
    """
    Runs an agentic loop with a local LLM.

    Handles:
    - Tool calling and execution
    - Conversation history management
    - API calls to Ollama, LM Studio, or OpenRouter
    - Output streaming
    - Completion detection
    """

    MAX_ITERATIONS = 50  # Safety limit for agent loop
    TOOL_RESULT_MAX_LENGTH = 10000  # Truncate long tool outputs

    def __init__(self, config: LLMProviderConfig, working_dir: str):
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx is required for local LLM support. Install with: pip install httpx")

        self.config = config
        self.working_dir = working_dir
        self.tools = get_all_tools(working_dir)
        self.tool_map = {tool.name: tool for tool in self.tools}
        self.conversation: List[Message] = []
        self._running = False
        self._waiting_input = False
        self._pending_input: Optional[str] = None
        self._output_buffer: List[str] = []

    def _build_system_prompt(self) -> str:
        """Build system prompt with tool descriptions and instructions"""
        tool_descriptions = "\n".join(
            f"- **{tool.name}**: {tool.description}"
            for tool in self.tools
        )

        return f"""You are an AI coding assistant working in the directory: {self.working_dir}

You have access to tools for reading, writing, and editing files, and executing shell commands.
Use these tools to complete coding tasks.

## Available Tools
{tool_descriptions}

## Instructions
1. First understand the task by reading relevant files
2. Make changes using the tools provided
3. Test your changes by running commands
4. When you have completed the task successfully, include "/complete" in your response

## Important Guidelines
- Read files before editing them to understand the context
- Make focused changes - don't refactor unrelated code
- Run tests or verification commands after making changes
- If you encounter errors, debug and fix them
- Always commit your changes with a descriptive message
- Say "/complete" when the task is fully done

Current time: {datetime.now().isoformat()}
"""

    async def _call_api(self, messages: List[Dict]) -> Dict:
        """Make API call to the LLM provider"""
        api_url = self.config.get_api_url()

        if self.config.provider_type == LLMProviderType.OLLAMA:
            return await self._call_ollama(api_url, messages)
        else:
            # LM Studio and OpenRouter use OpenAI-compatible API
            return await self._call_openai_compatible(api_url, messages)

    async def _call_ollama(self, api_url: str, messages: List[Dict]) -> Dict:
        """Call Ollama API"""
        tools_schema = [tool.to_openai_schema() for tool in self.tools]

        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                f"{api_url}/api/chat",
                json={
                    "model": self.config.model_name,
                    "messages": messages,
                    "tools": tools_schema,
                    "stream": False,
                    "options": {
                        "temperature": self.config.temperature,
                        "num_ctx": self.config.context_length,
                    }
                }
            )
            response.raise_for_status()
            return response.json()

    async def _call_openai_compatible(self, api_url: str, messages: List[Dict]) -> Dict:
        """Call OpenAI-compatible API (LM Studio, OpenRouter)"""
        tools_schema = [tool.to_openai_schema() for tool in self.tools]

        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        # OpenRouter requires additional headers
        if self.config.provider_type == LLMProviderType.OPENROUTER:
            headers["HTTP-Referer"] = "https://autowrkers.local"
            headers["X-Title"] = "Autowrkers"

        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                f"{api_url}/chat/completions",
                headers=headers,
                json={
                    "model": self.config.model_name,
                    "messages": messages,
                    "tools": tools_schema,
                    "temperature": self.config.temperature,
                    "max_tokens": min(4096, self.config.context_length // 2),
                }
            )
            response.raise_for_status()
            return response.json()

    def _parse_response(self, response: Dict) -> Message:
        """Parse API response into a Message"""
        if self.config.provider_type == LLMProviderType.OLLAMA:
            # Ollama format
            msg = response.get("message", {})
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls", [])
        else:
            # OpenAI format
            choice = response.get("choices", [{}])[0]
            msg = choice.get("message", {})
            content = msg.get("content") or ""
            tool_calls = msg.get("tool_calls", [])

        # Normalize tool calls format
        normalized_calls = []
        for call in tool_calls:
            if isinstance(call, dict):
                # Handle both formats
                func = call.get("function", call)
                normalized_calls.append({
                    "id": call.get("id", f"call_{len(normalized_calls)}"),
                    "type": "function",
                    "function": {
                        "name": func.get("name", ""),
                        "arguments": func.get("arguments", "{}")
                    }
                })

        return Message(
            role="assistant",
            content=content,
            tool_calls=normalized_calls
        )

    async def _execute_tool(self, tool_call: Dict) -> ToolResult:
        """Execute a tool call"""
        func = tool_call.get("function", {})
        name = func.get("name", "")
        args_str = func.get("arguments", "{}")

        tool = self.tool_map.get(name)
        if not tool:
            return ToolResult(False, "", f"Unknown tool: {name}")

        try:
            args = json.loads(args_str) if isinstance(args_str, str) else args_str
        except json.JSONDecodeError as e:
            return ToolResult(False, "", f"Invalid arguments JSON: {e}")

        try:
            result = await tool.execute(**args)

            # Truncate long outputs
            if len(result.output) > self.TOOL_RESULT_MAX_LENGTH:
                result = ToolResult(
                    result.success,
                    result.output[:self.TOOL_RESULT_MAX_LENGTH] + "\n... (truncated)",
                    result.error
                )

            return result
        except Exception as e:
            return ToolResult(False, "", f"Tool execution error: {e}")

    async def run(self, initial_prompt: str, output_callback=None) -> bool:
        """
        Run the agentic loop.

        Args:
            initial_prompt: The initial task/prompt
            output_callback: Async callback for output streaming

        Returns:
            True if completed successfully
        """
        self._running = True
        self._waiting_input = False

        # Initialize conversation
        system_msg = Message(role="system", content=self._build_system_prompt())
        user_msg = Message(role="user", content=initial_prompt)
        self.conversation = [system_msg, user_msg]

        await self._emit(output_callback, f"[Agent] Starting task...\n\n{initial_prompt}\n\n")

        iteration = 0
        while self._running and iteration < self.MAX_ITERATIONS:
            iteration += 1

            try:
                # Convert conversation to API format
                messages = [msg.to_dict() for msg in self.conversation]

                # Call LLM
                await self._emit(output_callback, f"[Agent] Thinking... (iteration {iteration})\n")
                response = await self._call_api(messages)

                # Parse response
                assistant_msg = self._parse_response(response)
                self.conversation.append(assistant_msg)

                # Emit assistant content
                if assistant_msg.content:
                    await self._emit(output_callback, f"\n{assistant_msg.content}\n")

                # Check for completion
                if "/complete" in assistant_msg.content.lower():
                    await self._emit(output_callback, "\n[Agent] Task completed!\n")
                    self._running = False
                    return True

                # Handle tool calls
                if assistant_msg.tool_calls:
                    for tool_call in assistant_msg.tool_calls:
                        func = tool_call.get("function", {})
                        tool_name = func.get("name", "unknown")
                        tool_args = func.get("arguments", "{}")

                        await self._emit(output_callback, f"\n[Tool] {tool_name}\n")

                        # Execute tool
                        result = await self._execute_tool(tool_call)

                        # Format output
                        if result.success:
                            output = result.output or "(no output)"
                        else:
                            output = f"Error: {result.error}\n{result.output}"

                        await self._emit(output_callback, f"{output}\n")

                        # Add tool result to conversation
                        tool_msg = Message(
                            role="tool",
                            content=output,
                            tool_call_id=tool_call.get("id"),
                            name=tool_name
                        )
                        self.conversation.append(tool_msg)
                else:
                    # No tool calls - might be waiting for input or just responding
                    # Check if it looks like a question
                    if assistant_msg.content.strip().endswith("?"):
                        self._waiting_input = True
                        await self._emit(output_callback, "\n[Agent] Waiting for input...\n")

                        # Wait for input
                        while self._waiting_input and self._running:
                            if self._pending_input:
                                user_input = self._pending_input
                                self._pending_input = None
                                self._waiting_input = False

                                self.conversation.append(Message(role="user", content=user_input))
                                await self._emit(output_callback, f"\n[User] {user_input}\n")
                                break
                            await asyncio.sleep(0.1)

            except httpx.HTTPStatusError as e:
                error_msg = f"API error: {e.response.status_code} - {e.response.text}"
                await self._emit(output_callback, f"\n[Error] {error_msg}\n")
                self._running = False
                return False

            except Exception as e:
                error_msg = f"Error: {str(e)}"
                await self._emit(output_callback, f"\n[Error] {error_msg}\n")
                self._running = False
                return False

        if iteration >= self.MAX_ITERATIONS:
            await self._emit(output_callback, f"\n[Agent] Reached maximum iterations ({self.MAX_ITERATIONS})\n")

        self._running = False
        return False

    async def send_input(self, message: str):
        """Send input to the running agent"""
        self._pending_input = message
        self._waiting_input = False

    def stop(self):
        """Stop the agent loop"""
        self._running = False
        self._waiting_input = False

    async def _emit(self, callback, text: str):
        """Emit text to callback and buffer"""
        self._output_buffer.append(text)
        if callback:
            await callback(text)

    def get_output(self) -> str:
        """Get all output as a string"""
        return "".join(self._output_buffer)


class LocalLLMProvider(LLMProvider):
    """
    Provider for local LLMs (Ollama, LM Studio, OpenRouter).

    Uses AgenticRunner to give the LLM tool-using capabilities.
    """

    def __init__(self, config: LLMProviderConfig, working_dir: str):
        super().__init__(config, working_dir)
        self.runner: Optional[AgenticRunner] = None
        self._task: Optional[asyncio.Task] = None

    async def start(self, initial_prompt: Optional[str] = None) -> bool:
        """Start a new LLM session with optional initial prompt"""
        try:
            await self._set_status(LLMProviderStatus.STARTING)

            self.runner = AgenticRunner(self.config, self.working_dir)

            if initial_prompt:
                # Start the agent loop in a background task
                self._task = asyncio.create_task(
                    self.runner.run(initial_prompt, self._handle_output)
                )
                await self._set_status(LLMProviderStatus.RUNNING)
            else:
                await self._set_status(LLMProviderStatus.WAITING_INPUT)

            return True

        except Exception as e:
            print(f"[LocalLLMProvider] Start error: {e}")
            await self._set_status(LLMProviderStatus.ERROR)
            return False

    async def send_input(self, message: str) -> bool:
        """Send input to the LLM"""
        if not self.runner:
            return False

        if self._task and not self._task.done():
            # Agent is running, send input
            await self.runner.send_input(message)
            await self._set_status(LLMProviderStatus.RUNNING)
        else:
            # Start new run with this input
            self._task = asyncio.create_task(
                self.runner.run(message, self._handle_output)
            )
            await self._set_status(LLMProviderStatus.RUNNING)

        return True

    async def stop(self) -> bool:
        """Stop the session"""
        if self.runner:
            self.runner.stop()

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        await self._set_status(LLMProviderStatus.STOPPED)
        return True

    async def _handle_output(self, text: str):
        """Handle output from the agent runner"""
        await self._emit_output(text)

        # Check for completion
        if "[Agent] Task completed!" in text:
            await self._set_status(LLMProviderStatus.COMPLETED)
        elif "[Agent] Waiting for input" in text:
            await self._set_status(LLMProviderStatus.WAITING_INPUT)
        elif "[Error]" in text:
            await self._set_status(LLMProviderStatus.ERROR)


async def test_llm_connection(config: LLMProviderConfig) -> Dict[str, Any]:
    """
    Test connection to an LLM provider.

    Returns dict with 'success', 'message', and optionally 'models'.
    """
    if not HTTPX_AVAILABLE:
        return {"success": False, "message": "httpx not installed"}

    api_url = config.get_api_url()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if config.provider_type == LLMProviderType.OLLAMA:
                # Test Ollama connection
                response = await client.get(f"{api_url}/api/tags")
                response.raise_for_status()
                data = response.json()
                models = [m.get("name") for m in data.get("models", [])]
                return {
                    "success": True,
                    "message": f"Connected to Ollama. {len(models)} models available.",
                    "models": models
                }

            else:
                # Test OpenAI-compatible endpoint
                headers = {"Content-Type": "application/json"}
                if config.api_key:
                    headers["Authorization"] = f"Bearer {config.api_key}"

                if config.provider_type == LLMProviderType.OPENROUTER:
                    headers["HTTP-Referer"] = "https://autowrkers.local"

                # Try to list models
                response = await client.get(f"{api_url}/models", headers=headers)
                response.raise_for_status()
                data = response.json()
                models = [m.get("id") for m in data.get("data", [])]

                return {
                    "success": True,
                    "message": f"Connected successfully. {len(models)} models available.",
                    "models": models[:50]  # Limit for display
                }

    except httpx.ConnectError:
        return {"success": False, "message": f"Cannot connect to {api_url}. Is the server running?"}
    except httpx.HTTPStatusError as e:
        return {"success": False, "message": f"HTTP error: {e.response.status_code}"}
    except Exception as e:
        return {"success": False, "message": str(e)}
