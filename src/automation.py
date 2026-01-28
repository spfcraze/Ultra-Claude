import asyncio
import subprocess
import re
import os
from datetime import datetime
from typing import Optional, Dict, List, Any, Tuple
from pathlib import Path

from .logging_config import get_logger
from .models import (
    project_manager, issue_session_manager,
    Project, IssueSession, IssueSessionStatus, ProjectStatus,
    VerificationResult
)
from .session_manager import manager as session_manager, SessionStatus
from .github_client import get_github_client, GitHubError

logger = get_logger("autowrkers.automation")


class IssueFixDetector:
    """Detects if an issue has already been worked on (fix commit or branch exists)"""

    @staticmethod
    def has_fix_commit(working_dir: str, issue_number: int) -> Tuple[bool, Optional[str]]:
        """
        Check if there's already a commit with 'Fix #N:' in the message.
        Returns (has_fix, commit_hash) tuple.
        """
        if not working_dir or not os.path.isdir(working_dir):
            return False, None

        try:
            # Search git log for fix commits matching this issue
            # Check both local and remote branches
            result = subprocess.run(
                ["git", "log", "--all", "--oneline", "--grep", f"Fix #{issue_number}:"],
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0 and result.stdout.strip():
                # Found matching commits
                lines = result.stdout.strip().split('\n')
                if lines:
                    commit_hash = lines[0].split()[0]
                    return True, commit_hash

            return False, None

        except Exception as e:
            print(f"[IssueFixDetector] Error checking for fix commit: {e}")
            return False, None

    @staticmethod
    def has_fix_branch(working_dir: str, issue_number: int) -> Tuple[bool, Optional[str]]:
        """
        Check if there's already a branch for this issue (fix/issue-N).
        Returns (has_branch, branch_name) tuple.
        """
        if not working_dir or not os.path.isdir(working_dir):
            return False, None

        branch_name = f"fix/issue-{issue_number}"

        try:
            # Check local branches
            result = subprocess.run(
                ["git", "branch", "--list", branch_name],
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0 and result.stdout.strip():
                return True, branch_name

            # Check remote branches
            result = subprocess.run(
                ["git", "branch", "-r", "--list", f"*/{branch_name}"],
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0 and result.stdout.strip():
                return True, branch_name

            return False, None

        except Exception as e:
            print(f"[IssueFixDetector] Error checking for fix branch: {e}")
            return False, None

    @staticmethod
    def is_issue_already_worked_on(working_dir: str, issue_number: int) -> Tuple[bool, str]:
        """
        Check if an issue has already been worked on.
        Returns (is_worked_on, reason) tuple.
        """
        # Check for fix commit
        has_commit, commit_hash = IssueFixDetector.has_fix_commit(working_dir, issue_number)
        if has_commit:
            return True, f"Fix commit exists: {commit_hash}"

        # Check for fix branch
        has_branch, branch_name = IssueFixDetector.has_fix_branch(working_dir, issue_number)
        if has_branch:
            return True, f"Fix branch exists: {branch_name}"

        return False, ""


class IssueComplexityAnalyzer:
    """Analyzes issue complexity to determine if it's suitable for automation"""

    COMPLEXITY_WEIGHTS = {
        "file_count": 2,
        "body_length": 1,
        "code_blocks": 3,
        "label_penalty": 5,
    }

    COMPLEX_LABELS = ["complex", "needs-discussion", "breaking-change", "architecture", "security"]

    @classmethod
    def analyze(cls, issue_session: IssueSession) -> Tuple[int, str]:
        """Returns (complexity_score, explanation). Higher = more complex."""
        score = 0
        reasons = []

        body = issue_session.github_issue_body or ""
        
        file_refs = ContextBuilder.extract_file_references(body)
        if len(file_refs) > 3:
            score += len(file_refs) * cls.COMPLEXITY_WEIGHTS["file_count"]
            reasons.append(f"{len(file_refs)} files mentioned")

        if len(body) > 2000:
            score += (len(body) // 1000) * cls.COMPLEXITY_WEIGHTS["body_length"]
            reasons.append(f"long description ({len(body)} chars)")

        code_blocks = body.count("```")
        if code_blocks > 4:
            score += code_blocks * cls.COMPLEXITY_WEIGHTS["code_blocks"]
            reasons.append(f"{code_blocks // 2} code blocks")

        for label in issue_session.github_issue_labels:
            if label.lower() in cls.COMPLEX_LABELS:
                score += cls.COMPLEXITY_WEIGHTS["label_penalty"]
                reasons.append(f"'{label}' label")

        explanation = ", ".join(reasons) if reasons else "standard issue"
        return score, explanation

    @classmethod
    def is_too_complex(cls, issue_session: IssueSession, threshold: int = 20) -> Tuple[bool, int, str]:
        """Check if issue is too complex for automation"""
        score, explanation = cls.analyze(issue_session)
        return score >= threshold, score, explanation


class ContextBuilder:
    """Builds context for Claude to understand the issue"""

    @staticmethod
    def extract_file_references(text: str) -> List[str]:
        """Extract file paths mentioned in text"""
        patterns = [
            r'`([^`]+\.[a-zA-Z]+)`',  # `file.ext`
            r'[\s\(]([a-zA-Z0-9_/\-\.]+\.[a-zA-Z]{1,5})[\s\)\,\:]',  # file.ext in text
            r'in\s+([a-zA-Z0-9_/\-\.]+\.[a-zA-Z]{1,5})',  # in file.ext
        ]

        files = set()
        for pattern in patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                if '/' in match or match.count('.') == 1:
                    files.add(match)

        return list(files)

    @staticmethod
    def extract_error_references(text: str) -> List[str]:
        """Extract function/class names from error messages"""
        patterns = [
            r'(\w+Error)',
            r'function\s+(\w+)',
            r'method\s+(\w+)',
            r'class\s+(\w+)',
        ]

        refs = set()
        for pattern in patterns:
            matches = re.findall(pattern, text)
            refs.update(matches)

        return list(refs)

    @staticmethod
    async def find_related_files(working_dir: str, references: List[str]) -> List[str]:
        """Find files related to the references"""
        related = []

        for ref in references:
            # Search for files containing the reference
            try:
                result = subprocess.run(
                    ["grep", "-rl", ref, working_dir, "--include=*.py",
                     "--include=*.js", "--include=*.ts", "--include=*.tsx"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode == 0:
                    files = result.stdout.strip().split('\n')
                    related.extend([f for f in files if f])
            except Exception:
                pass

        return list(set(related))[:10]  # Limit to 10 files

    @classmethod
    async def build_prompt(cls, project: Project, issue_session: IssueSession) -> str:
        """Build the full prompt for Claude"""
        # Extract references from issue
        file_refs = cls.extract_file_references(issue_session.github_issue_body)
        error_refs = cls.extract_error_references(issue_session.github_issue_body)

        # Find related files
        related_files = []
        if project.working_dir and os.path.isdir(project.working_dir):
            related_files = await cls.find_related_files(
                project.working_dir,
                file_refs + error_refs
            )

        # Store context files
        issue_session_manager.update(
            issue_session.id,
            context_files=file_refs + related_files
        )

        # Build prompt
        prompt = f"""You are working on GitHub issue #{issue_session.github_issue_number} in the {project.github_repo} repository.

## Issue Title
{issue_session.github_issue_title}

## Issue Description
{issue_session.github_issue_body}

## Labels
{', '.join(issue_session.github_issue_labels) if issue_session.github_issue_labels else 'None'}

## Working Directory
{project.working_dir}

## Branch
You are working on branch: {issue_session.branch_name}
"""

        if file_refs:
            prompt += f"""
## Files Mentioned in Issue
{chr(10).join('- ' + f for f in file_refs)}
"""

        if related_files:
            prompt += f"""
## Related Files (auto-detected)
{chr(10).join('- ' + f for f in related_files[:5])}
"""

        # Add verification commands if configured
        verification_info = []
        if project.lint_command:
            verification_info.append(f"- Lint: `{project.lint_command}`")
        if project.test_command:
            verification_info.append(f"- Tests: `{project.test_command}`")
        if project.build_command:
            verification_info.append(f"- Build: `{project.build_command}`")

        if verification_info:
            prompt += f"""
## Verification Commands
{chr(10).join(verification_info)}
"""

        prompt += """
## Instructions
1. First, understand the issue by reading the relevant code files
2. Create the fix branch if not already on it: `git checkout -b """ + issue_session.branch_name + """`
3. Implement a fix following the project's coding standards
4. Write or update tests if applicable
5. Run the verification commands to ensure the fix works
6. Commit your changes with a message referencing the issue: "Fix #""" + str(issue_session.github_issue_number) + """: <description>"
7. When complete and all tests pass, type: /complete

## Important
- Keep changes focused on the issue - don't refactor unrelated code
- If you need clarification about the issue, explain what's unclear
- If the issue cannot be fixed, explain why
"""

        return prompt


class VerificationRunner:
    """Runs verification checks on code changes"""

    @staticmethod
    async def run_command(command: str, working_dir: str, timeout: int = 300) -> VerificationResult:
        """Run a verification command"""
        start_time = datetime.now()

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=working_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )

            output = stdout.decode() + stderr.decode()
            passed = process.returncode == 0

            duration = int((datetime.now() - start_time).total_seconds() * 1000)

            return VerificationResult(
                check_type="command",
                passed=passed,
                output=output[-2000:],  # Limit output size
                duration_ms=duration
            )

        except asyncio.TimeoutError:
            return VerificationResult(
                check_type="command",
                passed=False,
                output=f"Command timed out after {timeout} seconds",
                duration_ms=timeout * 1000
            )
        except Exception as e:
            return VerificationResult(
                check_type="command",
                passed=False,
                output=str(e),
                duration_ms=0
            )

    @classmethod
    async def run_verification(cls, project: Project, issue_session: IssueSession) -> List[VerificationResult]:
        """Run all verification checks for a project"""
        results = []

        if not project.working_dir or not os.path.isdir(project.working_dir):
            return [VerificationResult(
                check_type="setup",
                passed=False,
                output="Working directory not configured or doesn't exist"
            )]

        # Run lint check
        if project.lint_command:
            result = await cls.run_command(project.lint_command, project.working_dir)
            result.check_type = "lint"
            results.append(result)
            issue_session_manager.add_verification_result(issue_session.id, result)

        # Run test check
        if project.test_command:
            result = await cls.run_command(project.test_command, project.working_dir)
            result.check_type = "test"
            results.append(result)
            issue_session_manager.add_verification_result(issue_session.id, result)

        # Run build check
        if project.build_command:
            result = await cls.run_command(project.build_command, project.working_dir)
            result.check_type = "build"
            results.append(result)
            issue_session_manager.add_verification_result(issue_session.id, result)

        # If no checks configured, just verify the branch exists and has commits
        if not results:
            result = await cls.run_command(
                f"git log {project.default_branch}..HEAD --oneline",
                project.working_dir
            )
            result.check_type = "commits"
            result.passed = bool(result.output.strip())
            if not result.passed:
                result.output = "No commits found on the fix branch"
            results.append(result)
            issue_session_manager.add_verification_result(issue_session.id, result)

        return results


class PRCreator:
    """Creates pull requests for completed issues"""

    @staticmethod
    async def push_branch(project: Project, branch_name: str) -> Tuple[bool, str]:
        """Push branch to remote using token authentication"""
        try:
            # Build authenticated remote URL (x-access-token format works with fine-grained PATs)
            token = project.get_token()
            auth_remote = f"https://x-access-token:{token}@github.com/{project.github_repo}.git"

            # Push to the authenticated URL
            process = await asyncio.create_subprocess_exec(
                "git", "push", auth_remote, branch_name, "-u",
                cwd=project.working_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                print(f"[Automation] Branch {branch_name} pushed successfully")
                return True, ""
            else:
                # Sanitize error message to not expose token
                error_msg = stderr.decode().replace(token, "***")
                print(f"[ERROR] Failed to push branch: {error_msg}")
                return False, error_msg
        except Exception as e:
            print(f"[ERROR] Failed to push branch: {e}")
            return False, str(e)

    @staticmethod
    async def create_pr(project: Project, issue_session: IssueSession) -> Optional[Dict[str, Any]]:
        """Create a pull request for the issue"""
        client = get_github_client(project.get_token())

        # Push branch first
        pushed, push_error = await PRCreator.push_branch(project, issue_session.branch_name)
        if not pushed:
            print(f"[ERROR] Failed to push branch for PR: {push_error}")
            return None

        # Build PR description
        pr_body = f"""## Summary
Fixes #{issue_session.github_issue_number}

## Changes
This PR addresses the issue: **{issue_session.github_issue_title}**

## Verification
"""

        # Add verification results
        for result in issue_session.verification_results:
            status = "✅" if result.passed else "❌"
            pr_body += f"- {status} {result.check_type.title()}\n"

        pr_body += """
---
🤖 This PR was automatically generated by [Autowrkers](https://github.com/autowrkers)
"""

        try:
            pr = await client.create_pull_request(
                repo=project.github_repo,
                title=f"Fix #{issue_session.github_issue_number}: {issue_session.github_issue_title}",
                body=pr_body,
                head=issue_session.branch_name,
                base=project.default_branch
            )

            # Update issue session
            issue_session_manager.update(
                issue_session.id,
                pr_number=pr.number,
                pr_url=pr.html_url,
                status=IssueSessionStatus.PR_CREATED
            )

            # Comment on the issue
            await client.create_issue_comment(
                repo=project.github_repo,
                issue_number=issue_session.github_issue_number,
                body=f"🤖 A fix has been implemented and submitted as PR #{pr.number}.\n\n[View Pull Request]({pr.html_url})"
            )

            return {"number": pr.number, "url": pr.html_url}

        except GitHubError as e:
            print(f"[ERROR] Failed to create PR: {e}")
            return None


class AutomationController:
    """Main automation controller"""

    MAX_LOG_ENTRIES = 100

    def __init__(self):
        self._running_projects: Dict[int, asyncio.Task] = {}
        self._project_status: Dict[int, Dict] = {}
        self._project_logs: Dict[int, List[Dict]] = {}
        self._event_callbacks: List[Any] = []
        self._setup_completion_callback()

    def add_event_callback(self, callback):
        self._event_callbacks.append(callback)

    async def recover_interrupted_sessions(self):
        """Recover issue sessions that were interrupted by server restart"""
        recovered = 0
        for project in project_manager.get_all():
            in_progress = issue_session_manager.get_in_progress(project.id)
            for issue_session in in_progress:
                if issue_session.status == IssueSessionStatus.IN_PROGRESS:
                    uc_session = session_manager.get_session(issue_session.session_id) if issue_session.session_id else None
                    
                    if not uc_session or uc_session.status == SessionStatus.STOPPED:
                        print(f"[Recovery] Issue #{issue_session.github_issue_number} was interrupted, marking for retry")
                        issue_session_manager.update(
                            issue_session.id,
                            status=IssueSessionStatus.PENDING,
                            last_error="Session interrupted by server restart - will retry"
                        )
                        recovered += 1
                    else:
                        print(f"[Recovery] Issue #{issue_session.github_issue_number} session still active")
                        
                elif issue_session.status == IssueSessionStatus.VERIFYING:
                    print(f"[Recovery] Issue #{issue_session.github_issue_number} was verifying, resetting to in_progress")
                    issue_session_manager.update(
                        issue_session.id,
                        status=IssueSessionStatus.PENDING,
                        last_error="Verification interrupted - will retry"
                    )
                    recovered += 1
        
        if recovered:
            print(f"[Recovery] Recovered {recovered} interrupted issue sessions")

    async def _emit_event(self, event_type: str, data: Dict):
        for callback in self._event_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event_type, data)
                else:
                    callback(event_type, data)
            except Exception as e:
                print(f"[Automation] Event callback error: {e}")

    def _setup_completion_callback(self):
        """Register callback to handle session completion signals"""
        async def on_session_completed(session_id: int):
            await self._handle_session_completion_signal(session_id)
        session_manager.add_completion_callback(on_session_completed)

    async def _handle_session_completion_signal(self, session_id: int):
        """Handle immediate completion when Claude signals /complete"""
        issue_session = issue_session_manager.get_by_session_id(session_id)
        if not issue_session:
            print(f"[Automation] No issue session found for session {session_id}")
            return

        project = project_manager.get(issue_session.project_id)
        if not project:
            print(f"[Automation] No project found for issue session {issue_session.id}")
            return

        self._log(project.id, f"Completion signal received for issue #{issue_session.github_issue_number}")
        await self._handle_completed_session(project, issue_session)

    def _log(self, project_id: int, message: str, level: str = "info"):
        """Add a log entry for a project"""
        if project_id not in self._project_logs:
            self._project_logs[project_id] = []

        entry = {
            "timestamp": datetime.now().isoformat(),
            "level": level,
            "message": message
        }
        self._project_logs[project_id].append(entry)

        # Keep only the last MAX_LOG_ENTRIES
        if len(self._project_logs[project_id]) > self.MAX_LOG_ENTRIES:
            self._project_logs[project_id] = self._project_logs[project_id][-self.MAX_LOG_ENTRIES:]

        # Also print to console
        print(f"[Automation:{project_id}] [{level.upper()}] {message}")

    def get_project_logs(self, project_id: int, limit: int = 50) -> List[Dict]:
        """Get recent log entries for a project"""
        logs = self._project_logs.get(project_id, [])
        return logs[-limit:] if limit else logs

    async def start_project(self, project_id: int):
        """Start automation for a project"""
        if project_id in self._running_projects:
            self._log(project_id, "Automation already running", "warn")
            return  # Already running

        self._log(project_id, "Starting automation...")
        task = asyncio.create_task(self._automation_loop(project_id))
        self._running_projects[project_id] = task
        self._project_status[project_id] = {
            "started_at": datetime.now().isoformat(),
            "issues_processed": 0,
            "issues_completed": 0,
            "issues_failed": 0
        }
        self._log(project_id, "Automation started successfully")

    async def stop_project(self, project_id: int):
        """Stop automation for a project"""
        if project_id in self._running_projects:
            self._log(project_id, "Stopping automation...")
            self._running_projects[project_id].cancel()
            try:
                await self._running_projects[project_id]
            except asyncio.CancelledError:
                pass
            del self._running_projects[project_id]
            self._log(project_id, "Automation stopped")

    def get_project_status(self, project_id: int) -> Dict:
        """Get automation status for a project"""
        return self._project_status.get(project_id, {
            "running": project_id in self._running_projects,
            "started_at": None,
            "issues_processed": 0,
            "issues_completed": 0,
            "issues_failed": 0
        })

    async def _automation_loop(self, project_id: int):
        """Main automation loop for a project"""
        self._log(project_id, "Automation loop started")

        while True:
            try:
                project = project_manager.get(project_id)
                if not project:
                    self._log(project_id, "Project not found, stopping", "error")
                    break

                # Sync issues if auto_sync is enabled
                if project.auto_sync:
                    self._log(project_id, "Syncing issues from GitHub...")
                    sync_count = await self._sync_issues(project)
                    if sync_count > 0:
                        self._log(project_id, f"Synced {sync_count} new issues")

                # Get pending issue sessions
                pending = issue_session_manager.get_pending(project_id)
                in_progress = issue_session_manager.get_in_progress(project_id)

                # Log current status
                self._log(project_id, f"Status: {len(pending)} pending, {len(in_progress)} in progress")

                # Start new sessions up to max_concurrent
                available_slots = project.max_concurrent - len(in_progress)

                for issue_session in pending[:available_slots]:
                    if project.auto_start:
                        self._log(project_id, f"Starting work on issue #{issue_session.github_issue_number}: {issue_session.github_issue_title[:50]}...")
                        await self.start_issue_session(issue_session)

                # Check for completed sessions
                await self._check_completed_sessions(project_id)

                # Wait before next iteration
                self._log(project_id, "Waiting 30 seconds before next check...")
                await asyncio.sleep(30)

            except asyncio.CancelledError:
                self._log(project_id, "Automation stopped by user")
                break
            except Exception as e:
                self._log(project_id, f"Error: {e}", "error")
                await asyncio.sleep(60)

    async def _sync_issues(self, project: Project) -> int:
        """Sync issues from GitHub. Returns count of new issues."""
        if not project.github_token_encrypted:
            return 0

        client = get_github_client(project.get_token())
        created_count = 0

        try:
            from .models import IssueFilter
            issues = await client.get_all_issues(
                project.github_repo,
                project.issue_filter if isinstance(project.issue_filter, IssueFilter) else None,
                max_issues=50
            )

            self._log(project.id, f"Found {len(issues)} open issues on GitHub")

            for issue in issues:
                existing = issue_session_manager.get_by_issue(project.id, issue.number)
                if not existing:
                    # Check if this issue already has a fix commit or branch
                    already_worked, reason = IssueFixDetector.is_issue_already_worked_on(
                        project.working_dir, issue.number
                    )
                    if already_worked:
                        self._log(project.id, f"Skipping issue #{issue.number}: {reason} (awaiting review/merge)", "info")
                        # Create issue session but mark as skipped so we don't check again
                        session = issue_session_manager.create(project.id, issue)
                        issue_session_manager.update(
                            session.id,
                            status=IssueSessionStatus.SKIPPED,
                            last_error=f"Already worked on: {reason}"
                        )
                        continue

                    issue_session_manager.create(project.id, issue)
                    self._log(project.id, f"Added issue #{issue.number}: {issue.title[:40]}...")
                    created_count += 1

            project_manager.update(project.id, last_sync=datetime.now().isoformat())
            return created_count

        except GitHubError as e:
            self._log(project.id, f"Failed to sync issues: {e}", "error")
            return 0

    async def start_issue_session(self, issue_session: IssueSession):
        """Start working on an issue"""
        project = project_manager.get(issue_session.project_id)
        if not project:
            self._log(issue_session.project_id, f"Project not found for issue #{issue_session.github_issue_number}", "error")
            return

        already_worked, reason = IssueFixDetector.is_issue_already_worked_on(
            project.working_dir, issue_session.github_issue_number
        )
        if already_worked:
            self._log(project.id, f"Skipping issue #{issue_session.github_issue_number}: {reason} (awaiting review/merge)", "warn")
            issue_session_manager.update(
                issue_session.id,
                status=IssueSessionStatus.SKIPPED,
                last_error=f"Already worked on: {reason}"
            )
            return

        too_complex, score, explanation = IssueComplexityAnalyzer.is_too_complex(issue_session)
        if too_complex:
            self._log(project.id, f"Issue #{issue_session.github_issue_number} too complex (score={score}): {explanation}", "warn")
            issue_session_manager.update(
                issue_session.id,
                status=IssueSessionStatus.NEEDS_REVIEW,
                last_error=f"Complexity score {score}: {explanation}"
            )
            return

        self._log(project.id, f"Starting work on issue #{issue_session.github_issue_number}: {issue_session.github_issue_title[:40]}...")

        try:
            # Update status
            issue_session_manager.update(
                issue_session.id,
                status=IssueSessionStatus.IN_PROGRESS,
                started_at=datetime.now().isoformat(),
                attempts=issue_session.attempts + 1
            )

            # Build the prompt
            self._log(project.id, "Building prompt with context...")
            prompt = await ContextBuilder.build_prompt(project, issue_session)
            self._log(project.id, f"Prompt built ({len(prompt)} chars)")

            # Create git branch first if working_dir is set
            if project.working_dir and os.path.isdir(project.working_dir):
                self._log(project.id, f"Setting up git branch: {issue_session.branch_name}")
                try:
                    # Checkout default branch and pull
                    result = subprocess.run(
                        ["git", "checkout", project.default_branch],
                        cwd=project.working_dir,
                        capture_output=True,
                        text=True
                    )
                    if result.returncode != 0:
                        self._log(project.id, f"Git checkout warning: {result.stderr}", "warn")

                    subprocess.run(
                        ["git", "pull"],
                        cwd=project.working_dir,
                        capture_output=True
                    )

                    # Create feature branch (might already exist)
                    result = subprocess.run(
                        ["git", "checkout", "-b", issue_session.branch_name],
                        cwd=project.working_dir,
                        capture_output=True,
                        text=True
                    )
                    if result.returncode != 0:
                        # Try checking out existing branch
                        subprocess.run(
                            ["git", "checkout", issue_session.branch_name],
                            cwd=project.working_dir,
                            capture_output=True
                        )
                    self._log(project.id, f"Git branch ready: {issue_session.branch_name}")
                except Exception as e:
                    self._log(project.id, f"Git branch setup failed: {e}", "error")
            else:
                self._log(project.id, f"Working directory not found: {project.working_dir}", "warn")

            # Create Autowrkers session with LLM configuration
            llm_provider = project.llm_provider or "claude_code"
            llm_config = project.get_llm_config() if not project.uses_claude_code() else None

            self._log(project.id, f"Creating session with {llm_provider} provider...")
            session = session_manager.create_session(
                name=f"Issue #{issue_session.github_issue_number}: {issue_session.github_issue_title[:30]}",
                working_dir=project.working_dir or os.getcwd(),
                initial_prompt=prompt,
                llm_provider_type=llm_provider,
                llm_config=llm_config
            )
            self._log(project.id, f"Session created: ID {session.id}")

            # Link session - set directly to avoid parameter conflict in update()
            issue_session.session_id = session.id
            issue_session_manager.update(issue_session.id, status=IssueSessionStatus.IN_PROGRESS)

            # Start the session
            self._log(project.id, "Starting Claude Code session...")
            await session_manager.start_session(session)
            self._log(project.id, f"Session {session.id} started successfully!")

            await self._emit_event("issue_started", {
                "project_id": project.id,
                "issue_session_id": issue_session.id,
                "issue_number": issue_session.github_issue_number,
                "issue_title": issue_session.github_issue_title,
                "session_id": session.id
            })

            # Update project status
            if issue_session.project_id in self._project_status:
                self._project_status[issue_session.project_id]["issues_processed"] += 1

        except Exception as e:
            self._log(project.id, f"Failed to start issue session: {e}", "error")
            # Reset to pending so it can be retried
            issue_session_manager.update(
                issue_session.id,
                status=IssueSessionStatus.FAILED,
                last_error=str(e)
            )

    async def _check_completed_sessions(self, project_id: int):
        """Check for sessions that have been marked complete"""
        project = project_manager.get(project_id)
        if not project:
            return

        # Get in-progress issue sessions
        in_progress = issue_session_manager.get_in_progress(project_id)

        for issue_session in in_progress:
            if not issue_session.session_id:
                continue

            # Check if Autowrkers session is completed
            uc_session = session_manager.get_session(issue_session.session_id)
            if not uc_session:
                continue

            if uc_session.status == SessionStatus.COMPLETED:
                await self._handle_completed_session(project, issue_session)

    async def _handle_completed_session(self, project: Project, issue_session: IssueSession):
        """Handle a completed Autowrkers session"""
        if issue_session.status not in (IssueSessionStatus.IN_PROGRESS, IssueSessionStatus.VERIFICATION_FAILED):
            print(f"[Automation] Session {issue_session.id} already being handled (status={issue_session.status})")
            return

        print(f"[Automation] Session {issue_session.id} completed, running verification")

        issue_session_manager.update(issue_session.id, status=IssueSessionStatus.VERIFYING)

        await self._emit_event("verification_started", {
            "project_id": project.id,
            "issue_session_id": issue_session.id,
            "issue_number": issue_session.github_issue_number
        })

        results = await VerificationRunner.run_verification(project, issue_session)
        all_passed = all(r.passed for r in results)

        if all_passed:
            print(f"[Automation] Verification passed for issue #{issue_session.github_issue_number}")

            await self._emit_event("verification_passed", {
                "project_id": project.id,
                "issue_session_id": issue_session.id,
                "issue_number": issue_session.github_issue_number
            })

            pr_result = await PRCreator.create_pr(project, issue_session)

            if pr_result:
                issue_session_manager.update(
                    issue_session.id,
                    status=IssueSessionStatus.PR_CREATED,
                    completed_at=datetime.now().isoformat()
                )

                if issue_session.project_id in self._project_status:
                    self._project_status[issue_session.project_id]["issues_completed"] += 1

                await self._emit_event("pr_created", {
                    "project_id": project.id,
                    "issue_session_id": issue_session.id,
                    "issue_number": issue_session.github_issue_number,
                    "pr_number": pr_result["number"],
                    "pr_url": pr_result["url"]
                })

                print(f"[Automation] PR created: {pr_result['url']}")
            else:
                issue_session_manager.update(
                    issue_session.id,
                    status=IssueSessionStatus.FAILED,
                    last_error="Failed to create pull request"
                )
        else:
            failed_checks = [r.check_type for r in results if not r.passed]
            error_msg = f"Verification failed: {', '.join(failed_checks)}"

            print(f"[Automation] Verification failed for issue #{issue_session.github_issue_number}: {error_msg}")

            await self._emit_event("verification_failed", {
                "project_id": project.id,
                "issue_session_id": issue_session.id,
                "issue_number": issue_session.github_issue_number,
                "failed_checks": failed_checks,
                "attempt": issue_session.attempts,
                "max_attempts": issue_session.max_attempts
            })

            if issue_session.attempts < issue_session.max_attempts:
                issue_session_manager.update(
                    issue_session.id,
                    status=IssueSessionStatus.VERIFICATION_FAILED,
                    last_error=error_msg
                )
                await self._retry_with_feedback(project, issue_session, results)
            else:
                issue_session_manager.update(
                    issue_session.id,
                    status=IssueSessionStatus.FAILED,
                    last_error=f"{error_msg} (max attempts reached)"
                )

                if issue_session.project_id in self._project_status:
                    self._project_status[issue_session.project_id]["issues_failed"] += 1

                await self._emit_event("issue_failed", {
                    "project_id": project.id,
                    "issue_session_id": issue_session.id,
                    "issue_number": issue_session.github_issue_number,
                    "error": error_msg
                })

                try:
                    client = get_github_client(project.get_token())
                    await client.create_issue_comment(
                        repo=project.github_repo,
                        issue_number=issue_session.github_issue_number,
                        body=f"🤖 Unable to automatically fix this issue after {issue_session.attempts} attempts.\n\nLast error: {error_msg}\n\nThis issue may require manual intervention."
                    )
                except Exception as e:
                    print(f"[Automation] Failed to comment on issue: {e}")

    async def _retry_with_feedback(self, project: Project, issue_session: IssueSession, results: List[VerificationResult]):
        """Retry the session with verification feedback"""
        # Build feedback message
        feedback = "The previous fix attempt failed verification. Here are the results:\n\n"

        for result in results:
            status = "✅ PASSED" if result.passed else "❌ FAILED"
            feedback += f"### {result.check_type.title()}: {status}\n"
            if not result.passed and result.output:
                feedback += f"```\n{result.output[:1000]}\n```\n\n"

        feedback += "\nPlease fix the issues and try again. When done, type /complete"

        # Send feedback to the session
        if issue_session.session_id:
            await session_manager.send_input(issue_session.session_id, feedback + "\r")

        # Update status
        issue_session_manager.update(
            issue_session.id,
            status=IssueSessionStatus.IN_PROGRESS
        )


# Global automation controller
automation_controller = AutomationController()
