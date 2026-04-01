"""
Agent Skills integration service (agentskills.io standard).

Discovers, installs, manages, and executes skills that extend the researcher
agent with additional instructions and tools. Skills are SKILL.md files
with YAML frontmatter following the AgentSkills open standard.

Two skill types:
- Instruction skills: SKILL.md body injected into researcher system prompt
- Tool-providing skills: Include tools.json defining callable tools with
  HTTP or script execution backends
"""

import asyncio
import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import yaml

from app.config import get_settings

logger = logging.getLogger(__name__)


# =============================================================================
# SKILL.md Parsing
# =============================================================================


def _parse_skill_md(path: Path) -> Optional[Dict[str, Any]]:
    """Parse a SKILL.md file, extracting YAML frontmatter and body.

    Returns dict with keys: name, description, license, compatibility,
    metadata, body. Returns None if the file is unparseable or missing
    required fields.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"Could not read {path}: {e}")
        return None

    # Split on --- delimiters for YAML frontmatter
    parts = text.split("---", 2)
    if len(parts) < 3:
        logger.warning(f"No YAML frontmatter in {path}")
        return None

    try:
        frontmatter = yaml.safe_load(parts[1])
    except yaml.YAMLError as e:
        logger.warning(f"Invalid YAML in {path}: {e}")
        return None

    if not isinstance(frontmatter, dict):
        logger.warning(f"Frontmatter is not a mapping in {path}")
        return None

    name = frontmatter.get("name", "")
    description = frontmatter.get("description", "")

    if not description:
        logger.warning(f"Skipping {path}: missing required 'description' field")
        return None

    if not name:
        # Fall back to directory name
        name = path.parent.name

    metadata = frontmatter.get("metadata", {}) or {}

    return {
        "name": name,
        "description": description,
        "license": frontmatter.get("license"),
        "compatibility": frontmatter.get("compatibility"),
        "metadata": metadata,
        "author": metadata.get("author"),
        "version": metadata.get("version"),
        "body": parts[2].strip(),
    }


def _load_tools_json(skill_dir: Path) -> Optional[List[Dict[str, Any]]]:
    """Load and validate tools.json from a skill directory.

    Returns a list of tool definitions or None if no tools.json exists.
    """
    tools_path = skill_dir / "tools.json"
    if not tools_path.exists():
        return None

    try:
        with open(tools_path, "r", encoding="utf-8") as f:
            tools = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Invalid tools.json in {skill_dir}: {e}")
        return None

    if not isinstance(tools, list):
        logger.warning(f"tools.json in {skill_dir} must be a JSON array")
        return None

    valid = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if not tool.get("name") or not tool.get("description"):
            logger.warning(f"Skipping tool without name/description in {skill_dir}")
            continue
        if "parameters" not in tool:
            tool["parameters"] = {
                "type": "object",
                "properties": {
                    "input": {
                        "type": "string",
                        "description": "Input for this tool",
                    }
                },
                "required": ["input"],
            }
        if "execution" not in tool:
            logger.warning(
                f"Tool '{tool['name']}' in {skill_dir} has no execution config, skipping"
            )
            continue
        valid.append(tool)

    return valid if valid else None


# =============================================================================
# SkillService
# =============================================================================


class SkillService:
    """Service for discovering, managing, and executing AgentSkills."""

    def __init__(self):
        self.settings = get_settings()
        self._skills_dir = self._resolve_skills_dir()

    def _resolve_skills_dir(self) -> Path:
        """Resolve the skills directory path."""
        raw = self.settings.skills_dir
        p = Path(raw)
        if p.is_absolute():
            return p
        # Relative: resolve from project root (3 levels up from app/services/skill_service.py)
        project_root = Path(__file__).parent.parent.parent
        return (project_root / raw).resolve()

    def _get_neo4j(self):
        from app.services.neo4j_service import get_neo4j_service
        return get_neo4j_service()

    # -----------------------------------------------------------------
    # Discovery
    # -----------------------------------------------------------------

    def discover_local_skills(self) -> int:
        """Scan skills directory for SKILL.md files and upsert into Neo4j.

        Returns the number of skills discovered.
        """
        if not self._skills_dir.exists():
            logger.info(f"Skills directory does not exist: {self._skills_dir}")
            return 0

        neo4j = self._get_neo4j()
        count = 0

        for entry in sorted(self._skills_dir.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("."):
                continue

            skill_md = entry / "SKILL.md"
            if not skill_md.exists():
                continue

            parsed = _parse_skill_md(skill_md)
            if not parsed:
                continue

            skill_id = entry.name
            tools_config = _load_tools_json(entry)
            tool_names = [t["name"] for t in tools_config] if tools_config else []

            # Check directory name vs frontmatter name mismatch
            if parsed["name"] != skill_id:
                logger.info(
                    f"Skill directory '{skill_id}' doesn't match frontmatter name "
                    f"'{parsed['name']}' — using directory name as ID"
                )

            neo4j.upsert_skill({
                "skill_id": skill_id,
                "name": parsed["name"],
                "description": parsed["description"],
                "version": str(parsed.get("version") or ""),
                "author": str(parsed.get("author") or ""),
                "license": str(parsed.get("license") or ""),
                "source": "local",
                "source_url": "",
                "skill_type": "tool" if tools_config else "instruction",
                "enabled": False,  # Only used for ON CREATE
                "installed_at": datetime.now(timezone.utc).isoformat(),
                "directory_path": str(entry),
                "tool_names": tool_names,
            })
            count += 1
            logger.info(
                f"Discovered skill: {skill_id} "
                f"(type={'tool' if tools_config else 'instruction'}, "
                f"tools={len(tool_names)})"
            )

        logger.info(f"Skill discovery complete: {count} skills found")
        return count

    # -----------------------------------------------------------------
    # Installation
    # -----------------------------------------------------------------

    async def install_from_url(self, url: str) -> dict:
        """Download and install a skill from a URL pointing to a SKILL.md file."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        content = resp.text

        # Parse the content as SKILL.md
        parsed = _parse_skill_md_from_string(content)
        if not parsed:
            raise ValueError("Could not parse SKILL.md from URL — missing frontmatter or description")

        skill_id = _sanitize_skill_id(parsed["name"])
        skill_dir = self._skills_dir / skill_id
        skill_dir.mkdir(parents=True, exist_ok=True)

        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

        tools_config = _load_tools_json(skill_dir)
        tool_names = [t["name"] for t in tools_config] if tools_config else []

        neo4j = self._get_neo4j()
        props = {
            "skill_id": skill_id,
            "name": parsed["name"],
            "description": parsed["description"],
            "version": str(parsed.get("version") or ""),
            "author": str(parsed.get("author") or ""),
            "license": str(parsed.get("license") or ""),
            "source": "url",
            "source_url": url,
            "skill_type": "tool" if tools_config else "instruction",
            "enabled": False,
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "directory_path": str(skill_dir),
            "tool_names": tool_names,
        }
        neo4j.upsert_skill(props)
        logger.info(f"Installed skill from URL: {skill_id}")
        return self._skill_node_to_info(neo4j.get_skill(skill_id))

    async def install_from_registry(self, registry_id: str) -> dict:
        """Install a skill from skills.sh registry.

        Accepts formats:
        - "owner/repo/skill-name" — fetches SKILL.md from GitHub
        - "owner/repo" — if repo contains a single skill at root
        """
        parts = registry_id.strip().split("/")
        if len(parts) < 2:
            raise ValueError(
                "registry_id must be 'owner/repo/skill-name' or 'owner/repo'"
            )

        owner = parts[0]
        repo = parts[1]
        skill_name = parts[2] if len(parts) > 2 else None

        # Try multiple GitHub raw URL patterns to find SKILL.md
        candidate_urls = []
        if skill_name:
            candidate_urls = [
                f"https://raw.githubusercontent.com/{owner}/{repo}/main/skills/{skill_name}/SKILL.md",
                f"https://raw.githubusercontent.com/{owner}/{repo}/main/{skill_name}/SKILL.md",
                f"https://raw.githubusercontent.com/{owner}/{repo}/master/skills/{skill_name}/SKILL.md",
                f"https://raw.githubusercontent.com/{owner}/{repo}/master/{skill_name}/SKILL.md",
            ]
        else:
            candidate_urls = [
                f"https://raw.githubusercontent.com/{owner}/{repo}/main/SKILL.md",
                f"https://raw.githubusercontent.com/{owner}/{repo}/master/SKILL.md",
            ]

        content = None
        used_url = None
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for url in candidate_urls:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        content = resp.text
                        used_url = url
                        break
                except Exception:
                    continue

        if not content:
            raise ValueError(
                f"Could not find SKILL.md for '{registry_id}'. "
                f"Tried: {', '.join(candidate_urls)}"
            )

        parsed = _parse_skill_md_from_string(content)
        if not parsed:
            raise ValueError(f"Could not parse SKILL.md from {used_url}")

        skill_id = _sanitize_skill_id(skill_name or parsed["name"])
        skill_dir = self._skills_dir / skill_id
        skill_dir.mkdir(parents=True, exist_ok=True)

        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

        tools_config = _load_tools_json(skill_dir)
        tool_names = [t["name"] for t in tools_config] if tools_config else []

        neo4j = self._get_neo4j()
        props = {
            "skill_id": skill_id,
            "name": parsed["name"],
            "description": parsed["description"],
            "version": str(parsed.get("version") or ""),
            "author": str(parsed.get("author") or ""),
            "license": str(parsed.get("license") or ""),
            "source": "registry",
            "source_url": used_url,
            "skill_type": "tool" if tools_config else "instruction",
            "enabled": False,
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "directory_path": str(skill_dir),
            "tool_names": tool_names,
        }
        neo4j.upsert_skill(props)
        logger.info(f"Installed skill from registry: {registry_id} → {skill_id}")
        return self._skill_node_to_info(neo4j.get_skill(skill_id))

    # -----------------------------------------------------------------
    # CRUD
    # -----------------------------------------------------------------

    def get_all_skills(self) -> List[dict]:
        """Get all installed skills."""
        neo4j = self._get_neo4j()
        return [self._skill_node_to_info(s) for s in neo4j.get_all_skills()]

    def get_skill(self, skill_id: str) -> Optional[dict]:
        """Get a skill with full SKILL.md body."""
        neo4j = self._get_neo4j()
        node = neo4j.get_skill(skill_id)
        if not node:
            return None

        info = self._skill_node_to_info(node)

        # Load SKILL.md body (tier 2)
        skill_dir = Path(node.get("directory_path", ""))
        skill_md = skill_dir / "SKILL.md"
        body = ""
        if skill_md.exists():
            parsed = _parse_skill_md(skill_md)
            if parsed:
                body = parsed.get("body", "")

        # Load tools config
        tools_config = None
        if skill_dir.exists():
            tools_config = _load_tools_json(skill_dir)

        info["body"] = body
        info["tools_config"] = tools_config
        return info

    def update_skill(self, skill_id: str, enabled: Optional[bool] = None) -> Optional[dict]:
        """Update a skill's settings."""
        neo4j = self._get_neo4j()
        props = {}
        if enabled is not None:
            props["enabled"] = enabled
        if not props:
            return self._skill_node_to_info(neo4j.get_skill(skill_id))

        node = neo4j.update_skill(skill_id, props)
        if not node:
            return None
        return self._skill_node_to_info(node)

    def delete_skill(self, skill_id: str) -> bool:
        """Delete a skill and its directory."""
        neo4j = self._get_neo4j()
        node = neo4j.get_skill(skill_id)
        if not node:
            return False

        # Remove directory if it exists and is inside our skills dir
        dir_path = Path(node.get("directory_path", ""))
        if dir_path.exists() and str(dir_path).startswith(str(self._skills_dir)):
            try:
                shutil.rmtree(dir_path)
                logger.info(f"Deleted skill directory: {dir_path}")
            except Exception as e:
                logger.warning(f"Could not delete skill directory {dir_path}: {e}")

        neo4j.delete_skill(skill_id)
        logger.info(f"Deleted skill: {skill_id}")
        return True

    # -----------------------------------------------------------------
    # Researcher Integration
    # -----------------------------------------------------------------

    def get_skill_catalog(self) -> List[dict]:
        """Get compact catalog of enabled skills for the system prompt.

        Returns only name + description (tier 1) — no filesystem reads.
        Used to populate <available_skills> so the agent knows what it
        can activate on demand.
        """
        neo4j = self._get_neo4j()
        enabled = neo4j.get_enabled_skills()
        return [
            {
                "skill_id": s.get("skill_id", ""),
                "name": s.get("name", s.get("skill_id", "")),
                "description": s.get("description", ""),
                "skill_type": s.get("skill_type", "instruction"),
            }
            for s in enabled
        ]

    def load_skill_for_activation(
        self, skill_id: str
    ) -> Tuple[str, List[dict], Dict[str, Tuple[str, str]], Dict[str, str]]:
        """Load a skill's full context for on-demand activation.

        Called when the agent invokes activate_skill. Reads from filesystem
        (tier 2 loading) only when needed.

        Returns:
            (instructions, tool_definitions, tool_map, config)
            - instructions: SKILL.md body wrapped in <skill> tags
            - tool_definitions: OpenAI function-calling defs from tools.json
            - tool_map: {namespaced_name: (skill_id, original_name)}
            - config: skill configuration values from config.json
        """
        neo4j = self._get_neo4j()
        node = neo4j.get_skill(skill_id)
        if not node:
            raise ValueError(f"Skill '{skill_id}' not found")
        if not node.get("enabled"):
            raise ValueError(f"Skill '{skill_id}' is not enabled")

        skill_name = node.get("name", skill_id)
        dir_path = Path(node.get("directory_path", ""))

        # Load instructions (tier 2)
        instructions = ""
        skill_md = dir_path / "SKILL.md"
        if skill_md.exists():
            parsed = _parse_skill_md(skill_md)
            if parsed and parsed.get("body"):
                instructions = f'<skill name="{skill_name}">\n{parsed["body"]}\n</skill>'

        # Load tool definitions
        tool_definitions = []
        tool_map: Dict[str, Tuple[str, str]] = {}
        tools_config = _load_tools_json(dir_path)
        if tools_config:
            for tool in tools_config[: self.settings.max_skill_tools]:
                namespaced = f"skill__{skill_id}__{tool['name']}"
                tool_definitions.append({
                    "type": "function",
                    "function": {
                        "name": namespaced,
                        "description": f"[Skill: {skill_name}] {tool['description']}",
                        "parameters": tool["parameters"],
                    },
                })
                tool_map[namespaced] = (skill_id, tool["name"])

        # Load configuration values
        config = self.get_skill_config(skill_id)

        return instructions, tool_definitions, tool_map, config

    # -----------------------------------------------------------------
    # Tool Execution
    # -----------------------------------------------------------------

    async def execute_skill_tool(
        self, skill_id: str, tool_name: str, arguments: dict
    ) -> str:
        """Execute a skill tool and return the result as a string."""
        neo4j = self._get_neo4j()
        node = neo4j.get_skill(skill_id)
        if not node:
            return f"Error: skill '{skill_id}' not found"

        dir_path = Path(node.get("directory_path", ""))
        tools_config = _load_tools_json(dir_path)
        if not tools_config:
            return f"Error: no tools.json found for skill '{skill_id}'"

        tool_config = next((t for t in tools_config if t["name"] == tool_name), None)
        if not tool_config:
            return f"Error: tool '{tool_name}' not found in skill '{skill_id}'"

        execution = tool_config.get("execution", {})
        exec_type = execution.get("type", "")

        if exec_type == "http":
            return await self._execute_http_tool(execution, arguments)
        elif exec_type == "script":
            return await self._execute_script_tool(execution, arguments, dir_path)
        else:
            return f"Error: unsupported execution type '{exec_type}'"

    async def _execute_http_tool(self, execution: dict, arguments: dict) -> str:
        """Execute an HTTP-based skill tool."""
        method = execution.get("method", "POST").upper()
        url = _substitute_env_vars(execution.get("url", ""))
        headers = {}
        for k, v in execution.get("headers", {}).items():
            headers[k] = _substitute_env_vars(v)

        timeout = self.settings.skill_http_timeout

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if method == "GET":
                    resp = await client.get(url, params=arguments, headers=headers)
                else:
                    resp = await client.request(
                        method, url, json=arguments, headers=headers
                    )
                resp.raise_for_status()
                return resp.text[:4000]
        except httpx.TimeoutException:
            return "Error: HTTP tool call timed out"
        except httpx.HTTPStatusError as e:
            return f"Error: HTTP {e.response.status_code} — {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {str(e)[:500]}"

    async def _execute_script_tool(
        self, execution: dict, arguments: dict, skill_dir: Path
    ) -> str:
        """Execute a script-based skill tool (requires enable_skill_scripts)."""
        if not self.settings.enable_skill_scripts:
            return "Error: script execution is disabled (set ENABLE_SKILL_SCRIPTS=true)"

        command = execution.get("command", "")
        if not command:
            return "Error: no command specified in execution config"

        timeout = execution.get("timeout", self.settings.skill_script_timeout)

        # Build minimal environment — only SKILL_* prefixed vars
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/tmp"),
        }
        for k, v in os.environ.items():
            if k.startswith("SKILL_"):
                env[k] = v

        # Split command into parts (no shell=True for security)
        parts = command.split()

        try:
            proc = await asyncio.create_subprocess_exec(
                *parts,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(skill_dir),
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=json.dumps(arguments).encode()),
                timeout=timeout,
            )

            if proc.returncode != 0:
                err = stderr.decode(errors="replace")[:500]
                return f"Error: script exited with code {proc.returncode}: {err}"

            return stdout.decode(errors="replace")[:4000]
        except asyncio.TimeoutError:
            return f"Error: script timed out after {timeout}s"
        except Exception as e:
            return f"Error: {str(e)[:500]}"

    # -----------------------------------------------------------------
    # Registry Search
    # -----------------------------------------------------------------

    async def search_registry(self, query: str) -> List[dict]:
        """Search the skills.sh registry using its search API.

        Uses the official skills.sh API endpoint:
        GET https://skills.sh/api/search?q={query}&limit={limit}
        Returns {skills: [{id, skillId, name, installs, source}]}
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://skills.sh/api/search",
                    params={"q": query.strip(), "limit": 20},
                )
                if resp.status_code != 200:
                    logger.warning(f"skills.sh search returned {resp.status_code}")
                    return []

                data = resp.json()
                skills_data = data.get("skills", [])

                return [
                    {
                        "namespace": item.get("source", ""),
                        "name": item.get("name", item.get("skillId", "")),
                        "description": "",
                        "install_count": item.get("installs"),
                        "download_url": (
                            f"https://raw.githubusercontent.com/"
                            f"{item.get('source', '')}/main/skills/"
                            f"{item.get('skillId', item.get('name', ''))}/SKILL.md"
                        ),
                    }
                    for item in skills_data
                    if item.get("name") or item.get("skillId")
                ]

        except Exception as e:
            logger.warning(f"Registry search failed: {e}")
            return []

    # -----------------------------------------------------------------
    # Skill Configuration
    # -----------------------------------------------------------------

    def get_skill_config(self, skill_id: str) -> Dict[str, str]:
        """Read config.json from a skill's directory. Returns empty dict if missing."""
        neo4j = self._get_neo4j()
        node = neo4j.get_skill(skill_id)
        if not node:
            return {}
        dir_path = Path(node.get("directory_path", ""))
        config_path = dir_path / "config.json"
        if not config_path.exists():
            return {}
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Could not read config.json for {skill_id}: {e}")
            return {}

    def save_skill_config(self, skill_id: str, config: Dict[str, str]) -> None:
        """Write config.json to a skill's directory."""
        neo4j = self._get_neo4j()
        node = neo4j.get_skill(skill_id)
        if not node:
            raise ValueError(f"Skill '{skill_id}' not found")
        dir_path = Path(node.get("directory_path", ""))
        if not dir_path.exists() or not str(dir_path).startswith(str(self._skills_dir)):
            raise ValueError(f"Invalid skill directory for '{skill_id}'")
        config_path = dir_path / "config.json"
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        logger.info(f"Saved config for skill '{skill_id}'")

    def get_skill_config_schema(self, skill_id: str) -> Optional[List[dict]]:
        """Read the config_schema from the Neo4j Skill node."""
        neo4j = self._get_neo4j()
        node = neo4j.get_skill(skill_id)
        if not node:
            return None
        raw = node.get("config_schema")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

    def save_skill_config_schema(self, skill_id: str, schema: List[dict]) -> None:
        """Store the config_schema as a JSON string on the Neo4j Skill node."""
        neo4j = self._get_neo4j()
        neo4j.update_skill(skill_id, {"config_schema": json.dumps(schema)})

    async def analyze_skill_config(self, skill_id: str) -> List[dict]:
        """Use the primary LLM to extract required config variables from SKILL.md.

        Sends the skill body to the LLM, which returns a JSON array of variable
        definitions. The schema is cached in the Neo4j node for future use.
        """
        from openai import AsyncOpenAI
        from app.config import get_settings

        detail = self.get_skill(skill_id)
        if not detail:
            raise ValueError(f"Skill '{skill_id}' not found")

        body = detail.get("body", "")
        if not body:
            self.save_skill_config_schema(skill_id, [])
            return []

        settings = get_settings()
        client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_api_base or None,
        )

        system_prompt = (
            "Analyze the following skill documentation and extract any configuration "
            "variables that a user would need to provide for this skill to work. "
            "Look for API tokens, authentication credentials, base URLs, API keys, "
            "or any placeholders the user must fill in.\n\n"
            "Return a JSON array of objects with these fields:\n"
            '- "name": variable name in SCREAMING_SNAKE_CASE (e.g. API_TOKEN)\n'
            '- "description": one-sentence explanation of what this variable is and where to find it\n'
            '- "required": boolean, true if the skill cannot function without it\n'
            '- "type": "secret" for tokens/passwords/API keys, "text" for URLs/identifiers\n'
            '- "auth_header": (only for auth tokens/keys) the exact HTTP header to set, '
            'using the variable name as placeholder. Examples: '
            '"Authorization: Bearer API_TOKEN", "X-API-Key: API_KEY". '
            "Omit this field for non-auth variables.\n\n"
            "Return an empty array [] if no configuration is needed.\n"
            "Return ONLY the JSON array, no markdown fences or explanation."
        )

        try:
            response = await client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": body},
                ],
                temperature=0,
                max_tokens=1000,
            )
            raw = response.choices[0].message.content or "[]"
            # Strip markdown code fences if present
            raw = raw.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```\w*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw)
                raw = raw.strip()
            schema = json.loads(raw)
            if not isinstance(schema, list):
                schema = []
        except Exception as e:
            logger.warning(f"LLM analysis failed for skill '{skill_id}': {e}")
            schema = []

        self.save_skill_config_schema(skill_id, schema)
        return schema

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    def _skill_node_to_info(self, node: Optional[dict]) -> Optional[dict]:
        """Convert a Neo4j Skill node dict to a SkillInfo-compatible dict."""
        if not node:
            return None
        tool_names = node.get("tool_names", [])
        if isinstance(tool_names, str):
            tool_names = [tool_names] if tool_names else []

        # Compute config_status from cached schema + config.json existence
        config_status = None
        raw_schema = node.get("config_schema")
        if raw_schema:
            try:
                schema = json.loads(raw_schema)
                required_vars = [v["name"] for v in schema if v.get("required", True)]
                if required_vars:
                    dir_path = Path(node.get("directory_path", ""))
                    config_path = dir_path / "config.json"
                    if config_path.exists():
                        try:
                            with open(config_path, "r", encoding="utf-8") as f:
                                config = json.load(f)
                            if all(config.get(v) for v in required_vars):
                                config_status = "configured"
                            else:
                                config_status = "needs_setup"
                        except Exception:
                            config_status = "needs_setup"
                    else:
                        config_status = "needs_setup"
            except (json.JSONDecodeError, TypeError):
                pass

        return {
            "skill_id": node.get("skill_id", ""),
            "name": node.get("name", ""),
            "description": node.get("description", ""),
            "version": node.get("version") or None,
            "author": node.get("author") or None,
            "license": node.get("license") or None,
            "source": node.get("source", "local"),
            "source_url": node.get("source_url") or None,
            "skill_type": node.get("skill_type", "instruction"),
            "enabled": bool(node.get("enabled", False)),
            "installed_at": node.get("installed_at", ""),
            "tool_count": len(tool_names),
            "tool_names": tool_names,
            "config_status": config_status,
        }


# =============================================================================
# Module-level helpers
# =============================================================================


def _parse_skill_md_from_string(content: str) -> Optional[Dict[str, Any]]:
    """Parse SKILL.md content from a raw string (same as _parse_skill_md but no file I/O)."""
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None

    try:
        frontmatter = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None

    if not isinstance(frontmatter, dict):
        return None

    description = frontmatter.get("description", "")
    if not description:
        return None

    name = frontmatter.get("name", "unknown-skill")
    metadata = frontmatter.get("metadata", {}) or {}

    return {
        "name": name,
        "description": description,
        "license": frontmatter.get("license"),
        "compatibility": frontmatter.get("compatibility"),
        "metadata": metadata,
        "author": metadata.get("author"),
        "version": metadata.get("version"),
        "body": parts[2].strip(),
    }


def _sanitize_skill_id(name: str) -> str:
    """Sanitize a skill name into a valid directory/ID name."""
    # Lowercase, replace non-alphanumeric with hyphens, collapse runs
    sanitized = re.sub(r"[^a-z0-9-]", "-", name.lower().strip())
    sanitized = re.sub(r"-+", "-", sanitized).strip("-")
    return sanitized or "unnamed-skill"


def _substitute_env_vars(value: str) -> str:
    """Substitute ${SKILL_*} env var references in a string.

    Only resolves env vars prefixed with SKILL_ for security.
    """
    def replacer(match):
        var_name = match.group(1)
        if var_name.startswith("SKILL_"):
            return os.environ.get(var_name, "")
        # Don't resolve non-SKILL vars
        return match.group(0)

    return re.sub(r"\$\{([A-Z_][A-Z0-9_]*)\}", replacer, value)


# =============================================================================
# Singleton
# =============================================================================

_skill_service: Optional[SkillService] = None


def get_skill_service() -> SkillService:
    global _skill_service
    if _skill_service is None:
        _skill_service = SkillService()
    return _skill_service
