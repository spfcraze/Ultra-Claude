"""
GitHub API Client for Autowrkers
Handles all GitHub API interactions with rate limiting and error handling
"""
import asyncio
import aiohttp
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from .models import GitHubIssue, IssueFilter


@dataclass
class RateLimitInfo:
    """GitHub API rate limit information"""
    remaining: int = 5000
    limit: int = 5000
    reset_at: Optional[datetime] = None


@dataclass
class PullRequest:
    """Represents a GitHub pull request"""
    number: int
    title: str
    html_url: str
    state: str
    merged: bool = False
    mergeable: Optional[bool] = None


class GitHubError(Exception):
    """Base exception for GitHub API errors"""
    pass


class GitHubAuthError(GitHubError):
    """Authentication error"""
    pass


class GitHubRateLimitError(GitHubError):
    """Rate limit exceeded"""
    def __init__(self, reset_at: datetime):
        self.reset_at = reset_at
        super().__init__(f"Rate limit exceeded. Resets at {reset_at}")


class GitHubNotFoundError(GitHubError):
    """Resource not found"""
    pass


class GitHubClient:
    """
    Async GitHub API client with rate limiting and retry logic
    """
    BASE_URL = "https://api.github.com"
    MAX_CONCURRENT_REQUESTS = 5

    def __init__(self, token: str):
        self.token = token
        self.rate_limit = RateLimitInfo()
        self._session: Optional[aiohttp.ClientSession] = None
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_REQUESTS)
        self._request_lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"token {self.token}",
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "Autowrkers/1.0"
                }
            )
        return self._session

    async def close(self):
        """Close the session"""
        if self._session and not self._session.closed:
            await self._session.close()

    def _update_rate_limit(self, headers: dict):
        """Update rate limit info from response headers"""
        if "X-RateLimit-Remaining" in headers:
            self.rate_limit.remaining = int(headers["X-RateLimit-Remaining"])
        if "X-RateLimit-Limit" in headers:
            self.rate_limit.limit = int(headers["X-RateLimit-Limit"])
        if "X-RateLimit-Reset" in headers:
            self.rate_limit.reset_at = datetime.fromtimestamp(
                int(headers["X-RateLimit-Reset"])
            )

    async def _wait_for_rate_limit(self):
        """Wait if rate limit is low"""
        if self.rate_limit.remaining < 10 and self.rate_limit.reset_at:
            wait_time = (self.rate_limit.reset_at - datetime.now()).total_seconds()
            if wait_time > 0:
                print(f"[GitHub] Rate limit low, waiting {wait_time:.0f}s")
                await asyncio.sleep(min(wait_time + 1, 60))

    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[dict] = None,
        params: Optional[dict] = None,
        retries: int = 3
    ) -> dict:
        """Make an API request with retry logic and concurrency control"""
        async with self._semaphore:
            async with self._request_lock:
                await self._wait_for_rate_limit()
            
            session = await self._get_session()
            url = f"{self.BASE_URL}{endpoint}"

            for attempt in range(retries):
                try:
                    print(f"[GitHub] {method} {url}")
                    async with session.request(
                        method,
                        url,
                        json=data,
                        params=params
                    ) as response:
                        self._update_rate_limit(dict(response.headers))

                        if response.status == 200 or response.status == 201:
                            return await response.json()
                        elif response.status == 204:
                            return {}
                        elif response.status == 401:
                            raise GitHubAuthError("Invalid or expired token")
                        elif response.status == 403:
                            if self.rate_limit.remaining == 0:
                                raise GitHubRateLimitError(self.rate_limit.reset_at)
                            error = await response.json()
                            raise GitHubError(f"Forbidden: {error.get('message', '')}")
                        elif response.status == 404:
                            raise GitHubNotFoundError(f"Not found: {endpoint}")
                        elif response.status == 422:
                            error = await response.json()
                            raise GitHubError(f"Validation failed: {error.get('message', '')} - {error.get('errors', [])}")
                        else:
                            error = await response.json()
                            raise GitHubError(f"API error {response.status}: {error.get('message', '')}")

                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    if attempt < retries - 1:
                        wait = 2 ** attempt
                        print(f"[GitHub] Request failed, retrying in {wait}s: {e}")
                        await asyncio.sleep(wait)
                    else:
                        raise GitHubError(f"Request failed after {retries} attempts: {e}")

            raise GitHubError("Request failed")

    # ==================== Repository ====================

    async def get_repo(self, repo: str) -> dict:
        """Get repository information"""
        return await self._request("GET", f"/repos/{repo}")

    async def verify_access(self, repo: str) -> bool:
        """Verify we have access to a repository"""
        try:
            result = await self.get_repo(repo)
            print(f"[GitHub] Successfully accessed repo: {repo}")
            return True
        except GitHubNotFoundError as e:
            print(f"[GitHub] Repo not found: {repo} - {e}")
            return False
        except GitHubAuthError as e:
            print(f"[GitHub] Auth error for repo {repo}: {e}")
            raise  # Re-raise auth errors so they can be handled separately
        except GitHubError as e:
            print(f"[GitHub] Error accessing repo {repo}: {e}")
            return False

    # ==================== Issues ====================

    async def get_issues(
        self,
        repo: str,
        filter: Optional[IssueFilter] = None,
        page: int = 1,
        per_page: int = 30
    ) -> List[GitHubIssue]:
        """Get issues from a repository"""
        params = {
            "state": filter.state if filter else "open",
            "page": page,
            "per_page": per_page,
            "sort": "created",
            "direction": "desc"
        }

        if filter:
            if filter.labels:
                params["labels"] = ",".join(filter.labels)
            if filter.assignee:
                params["assignee"] = filter.assignee
            if filter.milestone:
                params["milestone"] = filter.milestone

        data = await self._request("GET", f"/repos/{repo}/issues", params=params)

        issues = []
        for item in data:
            # Skip pull requests (they appear in issues API)
            if "pull_request" not in item:
                issue = GitHubIssue.from_api_response(item)
                # Apply exclude labels filter
                if filter and filter.exclude_labels:
                    if not any(l in issue.labels for l in filter.exclude_labels):
                        issues.append(issue)
                else:
                    issues.append(issue)

        return issues

    async def get_all_issues(
        self,
        repo: str,
        filter: Optional[IssueFilter] = None,
        max_issues: int = 100
    ) -> List[GitHubIssue]:
        """Get all issues (paginated)"""
        all_issues = []
        page = 1
        per_page = 30

        while len(all_issues) < max_issues:
            issues = await self.get_issues(repo, filter, page, per_page)
            if not issues:
                break
            all_issues.extend(issues)
            if len(issues) < per_page:
                break
            page += 1

        return all_issues[:max_issues]

    async def get_issue(self, repo: str, issue_number: int) -> GitHubIssue:
        """Get a specific issue"""
        data = await self._request("GET", f"/repos/{repo}/issues/{issue_number}")
        return GitHubIssue.from_api_response(data)

    async def create_issue_comment(
        self,
        repo: str,
        issue_number: int,
        body: str
    ) -> dict:
        """Create a comment on an issue"""
        return await self._request(
            "POST",
            f"/repos/{repo}/issues/{issue_number}/comments",
            data={"body": body}
        )

    async def update_issue_labels(
        self,
        repo: str,
        issue_number: int,
        labels: List[str]
    ) -> dict:
        """Update labels on an issue"""
        return await self._request(
            "PUT",
            f"/repos/{repo}/issues/{issue_number}/labels",
            data={"labels": labels}
        )

    async def add_issue_labels(
        self,
        repo: str,
        issue_number: int,
        labels: List[str]
    ) -> dict:
        """Add labels to an issue"""
        return await self._request(
            "POST",
            f"/repos/{repo}/issues/{issue_number}/labels",
            data={"labels": labels}
        )

    async def close_issue(self, repo: str, issue_number: int) -> dict:
        """Close an issue"""
        return await self._request(
            "PATCH",
            f"/repos/{repo}/issues/{issue_number}",
            data={"state": "closed"}
        )

    # ==================== Pull Requests ====================

    async def create_pull_request(
        self,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str
    ) -> PullRequest:
        """Create a pull request"""
        data = await self._request(
            "POST",
            f"/repos/{repo}/pulls",
            data={
                "title": title,
                "body": body,
                "head": head,
                "base": base
            }
        )
        return PullRequest(
            number=data["number"],
            title=data["title"],
            html_url=data["html_url"],
            state=data["state"],
            merged=data.get("merged", False),
            mergeable=data.get("mergeable")
        )

    async def get_pull_request(self, repo: str, pr_number: int) -> PullRequest:
        """Get a pull request"""
        data = await self._request("GET", f"/repos/{repo}/pulls/{pr_number}")
        return PullRequest(
            number=data["number"],
            title=data["title"],
            html_url=data["html_url"],
            state=data["state"],
            merged=data.get("merged", False),
            mergeable=data.get("mergeable")
        )

    async def update_pull_request(
        self,
        repo: str,
        pr_number: int,
        title: Optional[str] = None,
        body: Optional[str] = None,
        state: Optional[str] = None
    ) -> PullRequest:
        """Update a pull request"""
        data = {}
        if title:
            data["title"] = title
        if body:
            data["body"] = body
        if state:
            data["state"] = state

        result = await self._request(
            "PATCH",
            f"/repos/{repo}/pulls/{pr_number}",
            data=data
        )
        return PullRequest(
            number=result["number"],
            title=result["title"],
            html_url=result["html_url"],
            state=result["state"],
            merged=result.get("merged", False),
            mergeable=result.get("mergeable")
        )

    # ==================== Branches ====================

    async def get_branch(self, repo: str, branch: str) -> dict:
        """Get branch information"""
        return await self._request("GET", f"/repos/{repo}/branches/{branch}")

    async def get_default_branch(self, repo: str) -> str:
        """Get the default branch name"""
        repo_info = await self.get_repo(repo)
        return repo_info.get("default_branch", "main")

    # ==================== Commits ====================

    async def get_commits(
        self,
        repo: str,
        branch: str = None,
        path: str = None,
        per_page: int = 10
    ) -> List[dict]:
        """Get recent commits"""
        params = {"per_page": per_page}
        if branch:
            params["sha"] = branch
        if path:
            params["path"] = path

        return await self._request("GET", f"/repos/{repo}/commits", params=params)

    # ==================== Contents ====================

    async def get_file_content(self, repo: str, path: str, ref: str = None) -> str:
        """Get file content from repository"""
        params = {}
        if ref:
            params["ref"] = ref

        import base64
        data = await self._request("GET", f"/repos/{repo}/contents/{path}", params=params)

        if data.get("encoding") == "base64":
            return base64.b64decode(data["content"]).decode("utf-8")
        return data.get("content", "")

    async def get_tree(self, repo: str, ref: str = "HEAD", recursive: bool = True) -> List[dict]:
        """Get repository file tree"""
        params = {"recursive": "1"} if recursive else {}
        data = await self._request("GET", f"/repos/{repo}/git/trees/{ref}", params=params)
        return data.get("tree", [])

    # ==================== Search ====================

    async def search_code(
        self,
        repo: str,
        query: str,
        per_page: int = 10
    ) -> List[dict]:
        """Search code in repository"""
        search_query = f"{query} repo:{repo}"
        data = await self._request(
            "GET",
            "/search/code",
            params={"q": search_query, "per_page": per_page}
        )
        return data.get("items", [])


class GitHubClientPool:
    """
    Pool of GitHub clients for managing multiple tokens/projects
    """
    def __init__(self):
        self._clients: Dict[str, GitHubClient] = {}

    def get_client(self, token: str) -> GitHubClient:
        """Get or create a client for a token"""
        if token not in self._clients:
            self._clients[token] = GitHubClient(token)
        return self._clients[token]

    async def close_all(self):
        """Close all clients"""
        for client in self._clients.values():
            await client.close()
        self._clients.clear()


# Global client pool
github_pool = GitHubClientPool()


def get_github_client(token: str) -> GitHubClient:
    """Get a GitHub client for a token"""
    return github_pool.get_client(token)
