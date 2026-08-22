"""Infer CI/CD pipeline metadata from ECS task definitions and enrich deployments."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

GIT_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$", re.I)
GITHUB_REPO_RE = re.compile(
    r"(?:github\.com[:/]|git@github\.com:)(?P<owner>[^/\s]+)/(?P<repo>[^/\s.]+)",
    re.I,
)
GITLAB_REPO_RE = re.compile(
    r"(?:gitlab\.com[:/]|git@gitlab\.com:)(?P<path>[^\s.]+?)(?:\.git)?$",
    re.I,
)
BITBUCKET_REPO_RE = re.compile(
    r"(?:bitbucket\.org[:/]|git@bitbucket\.org:)(?P<workspace>[^/\s]+)/(?P<repo>[^/\s.]+)",
    re.I,
)

CI_ENV_KEYS = {
    "GITHUB_ACTIONS",
    "GITHUB_REPOSITORY",
    "GITHUB_SHA",
    "GITHUB_REF",
    "GITHUB_REF_NAME",
    "GITHUB_RUN_ID",
    "GITHUB_RUN_NUMBER",
    "GITHUB_SERVER_URL",
    "GITHUB_ACTOR",
    "GITHUB_HEAD_REF",
    "GITLAB_CI",
    "CI_PROJECT_PATH",
    "CI_PROJECT_URL",
    "CI_PROJECT_ID",
    "CI_COMMIT_SHA",
    "CI_COMMIT_SHORT_SHA",
    "CI_COMMIT_REF_NAME",
    "CI_COMMIT_BRANCH",
    "CI_PIPELINE_ID",
    "CI_PIPELINE_URL",
    "CI_JOB_ID",
    "CI_JOB_URL",
    "CI_SERVER_URL",
    "BITBUCKET_BUILD_NUMBER",
    "BITBUCKET_COMMIT",
    "BITBUCKET_BRANCH",
    "BITBUCKET_TAG",
    "BITBUCKET_REPO_FULL_NAME",
    "BITBUCKET_REPO_SLUG",
    "BITBUCKET_WORKSPACE",
    "BITBUCKET_PIPELINE_UUID",
    "BITBUCKET_GIT_HTTP_ORIGIN",
    "CODEBUILD_BUILD_ID",
    "CODEBUILD_BUILD_NUMBER",
    "CODEBUILD_RESOLVED_SOURCE_VERSION",
    "CODEBUILD_SOURCE_REPO_URL",
    "CODEBUILD_SOURCE_VERSION",
    "CODEBUILD_INITIATOR",
    "CIRCLECI",
    "CIRCLE_SHA1",
    "CIRCLE_BRANCH",
    "CIRCLE_TAG",
    "CIRCLE_BUILD_NUM",
    "CIRCLE_BUILD_URL",
    "CIRCLE_PROJECT_REPONAME",
    "CIRCLE_PROJECT_USERNAME",
    "CIRCLE_REPOSITORY_URL",
    "JENKINS_URL",
    "BUILD_NUMBER",
    "BUILD_URL",
    "JOB_NAME",
    "GIT_COMMIT",
    "GIT_BRANCH",
    "GIT_URL",
    "COMMIT_SHA",
    "GIT_SHA",
    "BRANCH_NAME",
    "REPO_URL",
    "REPOSITORY_URL",
    "CI_COMMIT",
    "CI_BRANCH",
    "CI_COMMIT_MESSAGE",
    "CI_COMMIT_TITLE",
    "CI_COMMIT_DESCRIPTION",
    "CI_COMMIT_AUTHOR",
    "CI_COMMIT_AUTHOR_NAME",
    "COMMIT_MESSAGE",
    "GIT_COMMIT_MESSAGE",
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "PIPELINE_URL",
    "BUILD_ID",
}


def _env_map(task_definition: Optional[Dict[str, Any]]) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not task_definition:
        return values
    for container in task_definition.get("containerDefinitions", []):
        for env in container.get("environment", []):
            name = str(env.get("name") or "").strip()
            value = str(env.get("value") or "").strip()
            if name and value:
                values[name] = value
        for label_name, label_value in (container.get("dockerLabels") or {}).items():
            key = str(label_name or "").strip()
            val = str(label_value or "").strip()
            if key and val:
                values[f"LABEL:{key}"] = val
    return values


def _first(env: Dict[str, str], *names: str) -> str:
    for name in names:
        value = env.get(name)
        if value:
            return value
    return ""


def _short_sha(value: str) -> str:
    if GIT_SHA_RE.match(value or ""):
        return value[:7]
    return value[:12] if value else ""


def _parse_github_repo(value: str) -> Tuple[str, str]:
    if "/" in value and "://" not in value and "@" not in value:
        owner, _, repo = value.partition("/")
        return owner, repo[:-4] if repo.endswith(".git") else repo
    match = GITHUB_REPO_RE.search(value or "")
    if match:
        return match.group("owner"), match.group("repo")
    return "", ""


def _parse_bitbucket_repo(value: str) -> Tuple[str, str]:
    if "/" in value and "://" not in value and "@" not in value:
        workspace, _, repo = value.partition("/")
        return workspace, repo[:-4] if repo.endswith(".git") else repo
    match = BITBUCKET_REPO_RE.search(value or "")
    if match:
        return match.group("workspace"), match.group("repo")
    return "", ""


def _image_commit_hint(container_images: Optional[List[Dict[str, str]]]) -> str:
    for item in container_images or []:
        image = str(item.get("image") or "")
        tag = image.rsplit(":", 1)[-1] if ":" in image else ""
        if GIT_SHA_RE.match(tag):
            return tag
    return ""


def _detect_provider(env: Dict[str, str]) -> str:
    if (
        env.get("GITHUB_ACTIONS", "").lower() == "true"
        or env.get("GITHUB_REPOSITORY")
        or env.get("GITHUB_RUN_ID")
    ):
        return "github_actions"
    if (
        env.get("GITLAB_CI", "").lower() == "true"
        or env.get("CI_PIPELINE_ID")
        or env.get("CI_PROJECT_PATH")
    ):
        return "gitlab_ci"
    if (
        env.get("BITBUCKET_BUILD_NUMBER")
        or env.get("BITBUCKET_PIPELINE_UUID")
        or env.get("BITBUCKET_REPO_FULL_NAME")
        or env.get("BITBUCKET_REPO_SLUG")
    ):
        return "bitbucket_pipelines"
    if env.get("CODEBUILD_BUILD_ID") or env.get("CODEBUILD_SOURCE_REPO_URL"):
        return "codebuild"
    if env.get("CIRCLECI", "").lower() == "true" or env.get("CIRCLE_BUILD_URL"):
        return "circleci"
    if env.get("JENKINS_URL") or (
        env.get("BUILD_URL") and "jenkins" in env.get("BUILD_URL", "").lower()
    ):
        return "jenkins"
    if any(key in env for key in CI_ENV_KEYS):
        return "ci"
    source = _first(env, "LABEL:org.opencontainers.image.source", "REPO_URL", "GIT_URL")
    if "github.com" in source.lower():
        return "github"
    if "gitlab.com" in source.lower():
        return "gitlab"
    if "bitbucket.org" in source.lower():
        return "bitbucket"
    return ""


PROVIDER_LABELS = {
    "github_actions": "GitHub Actions",
    "github": "GitHub",
    "gitlab_ci": "GitLab CI",
    "gitlab": "GitLab",
    "bitbucket_pipelines": "Bitbucket Pipelines",
    "bitbucket": "Bitbucket",
    "codebuild": "AWS CodeBuild",
    "circleci": "CircleCI",
    "jenkins": "Jenkins",
    "ci": "CI/CD",
}


def infer_cicd_from_task_definition(
    task_definition: Optional[Dict[str, Any]],
    container_images: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    env = _env_map(task_definition)
    provider = _detect_provider(env)
    commit = _first(
        env,
        "GITHUB_SHA",
        "CI_COMMIT_SHA",
        "CI_COMMIT_SHORT_SHA",
        "BITBUCKET_COMMIT",
        "CODEBUILD_RESOLVED_SOURCE_VERSION",
        "CIRCLE_SHA1",
        "GIT_COMMIT",
        "COMMIT_SHA",
        "GIT_SHA",
        "CI_COMMIT",
        "LABEL:org.opencontainers.image.revision",
    ) or _image_commit_hint(container_images)
    branch = _first(
        env,
        "GITHUB_REF_NAME",
        "GITHUB_HEAD_REF",
        "CI_COMMIT_REF_NAME",
        "CI_COMMIT_BRANCH",
        "BITBUCKET_BRANCH",
        "CIRCLE_BRANCH",
        "GIT_BRANCH",
        "BRANCH_NAME",
        "CI_BRANCH",
        "CODEBUILD_SOURCE_VERSION",
    )
    if branch.startswith("refs/heads/"):
        branch = branch[len("refs/heads/") :]
    if branch.startswith("refs/tags/"):
        branch = branch[len("refs/tags/") :]

    repo = ""
    pipeline_url = ""
    pipeline_id = ""
    build_number = ""
    actor = ""
    project_url = ""

    if provider in {"github_actions", "github"}:
        repo = _first(env, "GITHUB_REPOSITORY")
        if not repo:
            owner, name = _parse_github_repo(
                _first(env, "LABEL:org.opencontainers.image.source", "REPO_URL", "GIT_URL")
            )
            if owner and name:
                repo = f"{owner}/{name}"
        server = _first(env, "GITHUB_SERVER_URL") or "https://github.com"
        run_id = _first(env, "GITHUB_RUN_ID")
        pipeline_id = run_id
        build_number = _first(env, "GITHUB_RUN_NUMBER")
        actor = _first(env, "GITHUB_ACTOR")
        if repo and run_id:
            pipeline_url = f"{server.rstrip('/')}/{repo}/actions/runs/{run_id}"
        elif repo and commit:
            pipeline_url = f"{server.rstrip('/')}/{repo}/commit/{commit}"
        project_url = f"{server.rstrip('/')}/{repo}" if repo else ""

    elif provider in {"gitlab_ci", "gitlab"}:
        repo = _first(env, "CI_PROJECT_PATH")
        project_url = _first(env, "CI_PROJECT_URL")
        pipeline_url = _first(env, "CI_PIPELINE_URL", "CI_JOB_URL")
        pipeline_id = _first(env, "CI_PIPELINE_ID", "CI_JOB_ID")
        build_number = pipeline_id
        if not project_url and repo:
            server = (_first(env, "CI_SERVER_URL") or "https://gitlab.com").rstrip("/")
            project_url = f"{server}/{repo}"
        if not pipeline_url and project_url and pipeline_id:
            pipeline_url = f"{project_url}/-/pipelines/{pipeline_id}"

    elif provider in {"bitbucket_pipelines", "bitbucket"}:
        repo = _first(env, "BITBUCKET_REPO_FULL_NAME")
        if not repo:
            workspace = _first(env, "BITBUCKET_WORKSPACE")
            slug = _first(env, "BITBUCKET_REPO_SLUG")
            if workspace and slug:
                repo = f"{workspace}/{slug}"
            else:
                workspace, slug = _parse_bitbucket_repo(
                    _first(
                        env,
                        "BITBUCKET_GIT_HTTP_ORIGIN",
                        "LABEL:org.opencontainers.image.source",
                        "REPO_URL",
                    )
                )
                if workspace and slug:
                    repo = f"{workspace}/{slug}"
        build_number = _first(env, "BITBUCKET_BUILD_NUMBER")
        pipeline_id = _first(env, "BITBUCKET_PIPELINE_UUID") or build_number
        if repo:
            project_url = f"https://bitbucket.org/{repo}"
            if build_number:
                pipeline_url = (
                    f"{project_url}/pipelines/results/{build_number}"
                )
            elif commit:
                pipeline_url = f"{project_url}/commits/{commit}"

    elif provider == "codebuild":
        pipeline_id = _first(env, "CODEBUILD_BUILD_ID")
        build_number = _first(env, "CODEBUILD_BUILD_NUMBER")
        project_url = _first(env, "CODEBUILD_SOURCE_REPO_URL")
        repo = project_url
        actor = _first(env, "CODEBUILD_INITIATOR")
        if pipeline_id and ":" in pipeline_id:
            project_name = pipeline_id.split(":")[0]
            pipeline_url = (
                "https://console.aws.amazon.com/codesuite/codebuild/projects/"
                f"{quote(project_name)}/build/{quote(pipeline_id)}/history"
            )

    elif provider == "circleci":
        pipeline_url = _first(env, "CIRCLE_BUILD_URL")
        build_number = _first(env, "CIRCLE_BUILD_NUM")
        pipeline_id = build_number
        user = _first(env, "CIRCLE_PROJECT_USERNAME")
        name = _first(env, "CIRCLE_PROJECT_REPONAME")
        if user and name:
            repo = f"{user}/{name}"
            project_url = _first(env, "CIRCLE_REPOSITORY_URL") or f"https://github.com/{repo}"

    elif provider == "jenkins":
        pipeline_url = _first(env, "BUILD_URL")
        build_number = _first(env, "BUILD_NUMBER")
        pipeline_id = build_number
        project_url = _first(env, "JENKINS_URL")
        repo = _first(env, "JOB_NAME", "GIT_URL")

    else:
        repo = _first(env, "REPO_URL", "REPOSITORY_URL", "GIT_URL", "LABEL:org.opencontainers.image.source")
        project_url = repo
        pipeline_url = _first(env, "PIPELINE_URL", "BUILD_URL")
        build_number = _first(env, "BUILD_NUMBER", "BUILD_ID")
        pipeline_id = build_number

    signals = sorted(key for key in env if key in CI_ENV_KEYS or key.startswith("LABEL:org.opencontainers"))
    if not provider and not commit and not signals:
        return {
            "provider": "",
            "provider_label": "",
            "detected": False,
            "signals": [],
            "commits": [],
        }

    commit_message = _first(
        env,
        "CI_COMMIT_MESSAGE",
        "CI_COMMIT_TITLE",
        "COMMIT_MESSAGE",
        "GIT_COMMIT_MESSAGE",
        "CI_COMMIT_DESCRIPTION",
    )
    if commit_message:
        commit_message = " ".join(commit_message.split())[:240]
    commit_author = _first(
        env,
        "CI_COMMIT_AUTHOR",
        "CI_COMMIT_AUTHOR_NAME",
        "GIT_AUTHOR_NAME",
        "GITHUB_ACTOR",
    )
    commit_url = ""
    if provider in {"github_actions", "github"} and repo and commit:
        server = _first(env, "GITHUB_SERVER_URL") or "https://github.com"
        commit_url = f"{server.rstrip('/')}/{repo}/commit/{commit}"
    elif provider in {"gitlab_ci", "gitlab"} and project_url and commit:
        commit_url = f"{project_url.rstrip('/')}/-/commit/{commit}"
    elif provider in {"bitbucket_pipelines", "bitbucket"} and project_url and commit:
        commit_url = f"{project_url.rstrip('/')}/commits/{commit}"

    commits: List[Dict[str, Any]] = []
    if commit:
        commits.append(
            {
                "sha": commit,
                "short_sha": _short_sha(commit),
                "message": commit_message,
                "author": commit_author or actor,
                "authored_at": "",
                "url": commit_url,
                "branch": branch,
                "source": "task_definition",
            }
        )

    return {
        "provider": provider or "ci",
        "provider_label": PROVIDER_LABELS.get(provider or "ci", "CI/CD"),
        "detected": True,
        "repository": repo,
        "project_url": project_url,
        "branch": branch,
        "commit": commit,
        "commit_short": _short_sha(commit),
        "commit_message": commit_message,
        "commit_author": commit_author or actor,
        "commit_url": commit_url,
        "pipeline_id": pipeline_id,
        "build_number": build_number,
        "pipeline_url": pipeline_url,
        "actor": actor,
        "signals": signals[:20],
        "source": "task_definition",
        "commits": commits,
    }


def _http_json(url: str, headers: Dict[str, str]) -> Tuple[Optional[Dict[str, Any]], str]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict):
                return payload, ""
            return {"data": payload}, ""
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def enrich_cicd_status(
    cicd: Dict[str, Any],
    tokens: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Optionally probe GitHub / GitLab / Bitbucket for live pipeline status."""
    item = dict(cicd or {})
    if not item.get("detected"):
        item["status"] = "PASS"
        item["message"] = "No CI/CD signals found in the task definition"
        return item

    tokens = tokens or {}
    provider = item.get("provider") or ""
    label = item.get("provider_label") or "CI/CD"
    bits = [label]
    if item.get("repository"):
        bits.append(str(item["repository"]))
    if item.get("branch"):
        bits.append(f"branch {item['branch']}")
    if item.get("commit_short"):
        bits.append(f"commit {item['commit_short']}")
    if item.get("build_number"):
        bits.append(f"build #{item['build_number']}")

    item["status"] = "PASS"
    item["message"] = " · ".join(bits)

    github_token = tokens.get("github_token") or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    gitlab_token = tokens.get("gitlab_token") or os.environ.get("GITLAB_TOKEN")
    bitbucket_user = tokens.get("bitbucket_username") or os.environ.get("BITBUCKET_USERNAME")
    bitbucket_token = (
        tokens.get("bitbucket_token")
        or os.environ.get("BITBUCKET_TOKEN")
        or os.environ.get("BITBUCKET_APP_PASSWORD")
    )

    if provider in {"github_actions", "github"} and github_token and item.get("repository"):
        repo = item["repository"]
        run_id = item.get("pipeline_id")
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {github_token}",
            "User-Agent": "ecs-service-doctor",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if run_id:
            payload, error = _http_json(
                f"https://api.github.com/repos/{repo}/actions/runs/{run_id}",
                headers,
            )
            if payload:
                conclusion = payload.get("conclusion") or payload.get("status") or ""
                item["pipeline_status"] = conclusion
                item["pipeline_url"] = payload.get("html_url") or item.get("pipeline_url")
                item["message"] = f"{item['message']} · Actions {conclusion}"
                if conclusion in {"failure", "timed_out", "cancelled"}:
                    item["status"] = "WARN"
            elif error:
                item["pipeline_status"] = "unknown"
                item["message"] = f"{item['message']} · Actions not probed ({error})"
        elif item.get("commit"):
            payload, error = _http_json(
                f"https://api.github.com/repos/{repo}/commits/{item['commit']}/status",
                headers,
            )
            if payload:
                state = payload.get("state") or ""
                item["pipeline_status"] = state
                item["message"] = f"{item['message']} · commit status {state}"
                if state in {"failure", "error"}:
                    item["status"] = "WARN"
            elif error:
                item["message"] = f"{item['message']} · commit status not probed ({error})"

    elif provider in {"gitlab_ci", "gitlab"} and gitlab_token and item.get("pipeline_id"):
        project = quote(str(item.get("repository") or ""), safe="")
        server = "https://gitlab.com"
        if item.get("project_url"):
            parts = str(item["project_url"]).rstrip("/").split("/")
            if len(parts) >= 3:
                server = "/".join(parts[:3])
        headers = {
            "PRIVATE-TOKEN": gitlab_token,
            "User-Agent": "ecs-service-doctor",
        }
        payload, error = _http_json(
            f"{server}/api/v4/projects/{project}/pipelines/{item['pipeline_id']}",
            headers,
        )
        if payload:
            state = payload.get("status") or ""
            item["pipeline_status"] = state
            item["pipeline_url"] = payload.get("web_url") or item.get("pipeline_url")
            item["message"] = f"{item['message']} · pipeline {state}"
            if state in {"failed", "canceled", "cancelled"}:
                item["status"] = "WARN"
        elif error:
            item["message"] = f"{item['message']} · pipeline not probed ({error})"

    elif (
        provider in {"bitbucket_pipelines", "bitbucket"}
        and bitbucket_user
        and bitbucket_token
        and item.get("repository")
        and item.get("pipeline_id")
    ):
        import base64

        auth = base64.b64encode(f"{bitbucket_user}:{bitbucket_token}".encode()).decode()
        headers = {
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
            "User-Agent": "ecs-service-doctor",
        }
        pipeline_id = str(item["pipeline_id"]).strip("{}")
        payload, error = _http_json(
            f"https://api.bitbucket.org/2.0/repositories/{item['repository']}/pipelines/{pipeline_id}",
            headers,
        )
        if payload:
            state = ((payload.get("state") or {}).get("result") or {}).get("name") or (
                (payload.get("state") or {}).get("name") or ""
            )
            item["pipeline_status"] = state
            item["message"] = f"{item['message']} · pipeline {state}"
            if str(state).upper() in {"FAILED", "ERROR", "STOPPED"}:
                item["status"] = "WARN"
        elif error:
            item["message"] = f"{item['message']} · pipeline not probed ({error})"

    return item


def _normalize_commit(
    sha: str,
    message: str = "",
    author: str = "",
    authored_at: str = "",
    url: str = "",
    branch: str = "",
    source: str = "api",
) -> Dict[str, Any]:
    return {
        "sha": sha,
        "short_sha": _short_sha(sha),
        "message": " ".join((message or "").split())[:240],
        "author": author,
        "authored_at": authored_at,
        "url": url,
        "branch": branch,
        "source": source,
    }


def enrich_commit_history(
    cicd: Dict[str, Any],
    tokens: Optional[Dict[str, str]] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """Fetch the deployed commit and recent branch commits from GitHub/GitLab/Bitbucket."""
    item = dict(cicd or {})
    commits: List[Dict[str, Any]] = list(item.get("commits") or [])
    if not item.get("detected") or not item.get("repository"):
        item["commits"] = commits
        return item

    tokens = tokens or {}
    provider = item.get("provider") or ""
    repo = str(item.get("repository") or "")
    sha = str(item.get("commit") or "")
    branch = str(item.get("branch") or "")
    limit = max(1, min(int(limit or 5), 10))

    github_token = tokens.get("github_token") or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    gitlab_token = tokens.get("gitlab_token") or os.environ.get("GITLAB_TOKEN")
    bitbucket_user = tokens.get("bitbucket_username") or os.environ.get("BITBUCKET_USERNAME")
    bitbucket_token = (
        tokens.get("bitbucket_token")
        or os.environ.get("BITBUCKET_TOKEN")
        or os.environ.get("BITBUCKET_APP_PASSWORD")
    )

    fetched: List[Dict[str, Any]] = []

    if provider in {"github_actions", "github"} and github_token:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {github_token}",
            "User-Agent": "ecs-service-doctor",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if sha and not item.get("commit_message"):
            payload, _error = _http_json(
                f"https://api.github.com/repos/{repo}/commits/{sha}",
                headers,
            )
            if payload:
                commit_body = payload.get("commit") or {}
                author = ((commit_body.get("author") or {}).get("name")) or (
                    (payload.get("author") or {}).get("login") or ""
                )
                item["commit_message"] = (commit_body.get("message") or "").split("\n")[0][:240]
                item["commit_author"] = author
                item["commit_authored_at"] = (commit_body.get("author") or {}).get("date") or ""
                item["commit_url"] = payload.get("html_url") or item.get("commit_url")
        ref = branch or sha
        if ref:
            payload, _error = _http_json(
                f"https://api.github.com/repos/{repo}/commits?sha={quote(ref)}&per_page={limit}",
                headers,
            )
            rows = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
            if isinstance(rows, list):
                for row in rows[:limit]:
                    commit_body = row.get("commit") or {}
                    author = ((commit_body.get("author") or {}).get("name")) or (
                        (row.get("author") or {}).get("login") or ""
                    )
                    fetched.append(
                        _normalize_commit(
                            sha=str(row.get("sha") or ""),
                            message=(commit_body.get("message") or "").split("\n")[0],
                            author=author,
                            authored_at=(commit_body.get("author") or {}).get("date") or "",
                            url=str(row.get("html_url") or ""),
                            branch=branch,
                            source="github",
                        )
                    )

    elif provider in {"gitlab_ci", "gitlab"} and gitlab_token:
        project = quote(repo, safe="")
        server = "https://gitlab.com"
        if item.get("project_url"):
            parts = str(item["project_url"]).rstrip("/").split("/")
            if len(parts) >= 3:
                server = "/".join(parts[:3])
        headers = {
            "PRIVATE-TOKEN": gitlab_token,
            "User-Agent": "ecs-service-doctor",
        }
        if sha and not item.get("commit_message"):
            payload, _error = _http_json(
                f"{server}/api/v4/projects/{project}/repository/commits/{sha}",
                headers,
            )
            if payload:
                item["commit_message"] = str(payload.get("title") or payload.get("message") or "")[:240]
                item["commit_author"] = str(payload.get("author_name") or "")
                item["commit_authored_at"] = str(payload.get("authored_date") or payload.get("created_at") or "")
                item["commit_url"] = str(payload.get("web_url") or item.get("commit_url") or "")
        ref = branch or sha
        if ref:
            payload, _error = _http_json(
                f"{server}/api/v4/projects/{project}/repository/commits?ref_name={quote(ref)}&per_page={limit}",
                headers,
            )
            rows = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
            if isinstance(rows, list):
                for row in rows[:limit]:
                    fetched.append(
                        _normalize_commit(
                            sha=str(row.get("id") or ""),
                            message=str(row.get("title") or row.get("message") or ""),
                            author=str(row.get("author_name") or ""),
                            authored_at=str(row.get("authored_date") or row.get("created_at") or ""),
                            url=str(row.get("web_url") or ""),
                            branch=branch,
                            source="gitlab",
                        )
                    )

    elif (
        provider in {"bitbucket_pipelines", "bitbucket"}
        and bitbucket_user
        and bitbucket_token
    ):
        import base64

        auth = base64.b64encode(f"{bitbucket_user}:{bitbucket_token}".encode()).decode()
        headers = {
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
            "User-Agent": "ecs-service-doctor",
        }
        if sha and not item.get("commit_message"):
            payload, _error = _http_json(
                f"https://api.bitbucket.org/2.0/repositories/{repo}/commit/{sha}",
                headers,
            )
            if payload:
                item["commit_message"] = str(payload.get("message") or "").split("\n")[0][:240]
                item["commit_author"] = str(
                    ((payload.get("author") or {}).get("user") or {}).get("display_name")
                    or (payload.get("author") or {}).get("raw")
                    or ""
                )
                item["commit_authored_at"] = str(payload.get("date") or "")
                links = ((payload.get("links") or {}).get("html") or {})
                item["commit_url"] = str(links.get("href") or item.get("commit_url") or "")
        ref = branch or sha
        if ref:
            payload, _error = _http_json(
                f"https://api.bitbucket.org/2.0/repositories/{repo}/commits/?include={quote(ref)}&pagelen={limit}",
                headers,
            )
            rows = (payload or {}).get("values") if isinstance(payload, dict) else None
            if isinstance(rows, list):
                for row in rows[:limit]:
                    links = ((row.get("links") or {}).get("html") or {})
                    fetched.append(
                        _normalize_commit(
                            sha=str(row.get("hash") or ""),
                            message=str(row.get("message") or "").split("\n")[0],
                            author=str(
                                ((row.get("author") or {}).get("user") or {}).get("display_name")
                                or (row.get("author") or {}).get("raw")
                                or ""
                            ),
                            authored_at=str(row.get("date") or ""),
                            url=str(links.get("href") or ""),
                            branch=branch,
                            source="bitbucket",
                        )
                    )

    if fetched:
        # Prefer API history; keep task-definition commit first if missing from fetch.
        seen = {entry["sha"] for entry in fetched if entry.get("sha")}
        merged = list(fetched)
        for entry in commits:
            if entry.get("sha") and entry["sha"] not in seen:
                merged.insert(0, entry)
        item["commits"] = merged[:limit]
        if item.get("commit_message") and item.get("commit_short"):
            item["message"] = (
                f"{item.get('message')} · \"{item['commit_message']}\""
                if item.get("message")
                else item["commit_message"]
            )
    else:
        # Refresh primary commit entry with any enriched fields.
        if commits:
            commits[0]["message"] = item.get("commit_message") or commits[0].get("message")
            commits[0]["author"] = item.get("commit_author") or commits[0].get("author")
            commits[0]["authored_at"] = item.get("commit_authored_at") or commits[0].get("authored_at")
            commits[0]["url"] = item.get("commit_url") or commits[0].get("url")
        item["commits"] = commits

    return item


def evaluate_cicd(
    service: Dict[str, Any],
    task_definition: Optional[Dict[str, Any]],
    container_images: Optional[List[Dict[str, str]]] = None,
    tokens: Optional[Dict[str, str]] = None,
    commit_limit: int = 5,
) -> Dict[str, Any]:
    cicd = infer_cicd_from_task_definition(task_definition, container_images)
    cicd = enrich_cicd_status(cicd, tokens=tokens)
    cicd = enrich_commit_history(cicd, tokens=tokens, limit=commit_limit)

    deployments = []
    for deployment in service.get("deployments", []):
        deployments.append(
            {
                "id": deployment.get("id"),
                "status": deployment.get("status"),
                "rollout_state": deployment.get("rolloutState"),
                "task_definition": (deployment.get("taskDefinition") or "").rsplit("/", 1)[-1],
                "desired": deployment.get("desiredCount"),
                "running": deployment.get("runningCount"),
                "pending": deployment.get("pendingCount"),
                "failed_tasks": deployment.get("failedTasks"),
                "created_at": str(deployment.get("createdAt") or ""),
                "updated_at": str(deployment.get("updatedAt") or ""),
            }
        )

    primary = next((item for item in deployments if item.get("status") == "PRIMARY"), None)
    message_parts = []
    if cicd.get("detected"):
        message_parts.append(cicd.get("message") or cicd.get("provider_label"))
    if primary:
        message_parts.append(
            f"ECS {primary.get('rollout_state') or primary.get('status')} · {primary.get('task_definition')}"
        )
    elif deployments:
        message_parts.append(f"{len(deployments)} ECS deployment(s)")
    else:
        message_parts.append("No ECS deployments listed")

    status = cicd.get("status") or "PASS"
    if primary and primary.get("rollout_state") and primary["rollout_state"] != "COMPLETED":
        if primary["rollout_state"] == "FAILED":
            status = "FAIL"
        elif status == "PASS":
            status = "WARN"

    return {
        "status": status,
        "message": " · ".join(part for part in message_parts if part),
        "cicd": cicd,
        "commits": cicd.get("commits") or [],
        "deployments": deployments,
    }
