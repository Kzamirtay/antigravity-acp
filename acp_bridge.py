#!/usr/bin/env python3
"""
Antigravity ACP Bridge / Inspector
----------------------------------
Transparent stdio middleware between ACP clients (like Paseo) and agy_acp_server.par.

Transforms and enriches Antigravity tool notifications:
- Injects file content and line metadata into 'read' (view_file) calls so clients
  can see exactly what the agent reads.
- Normalizes tool input fields (e.g. AbsolutePath -> filePath, StartLine -> line/offset,
  CommandLine -> command) to standard ACP client schemas.
- Enhances 'edit', 'write', 'search', and 'execute' tool calls with unified diffs and outputs.
"""

import sys
import os
import json
import signal
import subprocess
import threading
from pathlib import Path
from typing import Dict, Any, Optional

BASE_DIR = Path(__file__).resolve().parent
SERVER_BIN = BASE_DIR / "agy_acp_server.par"
LIB_IPV4_SO = BASE_DIR / "libforce_ipv4.so"
HARNESS_BIN = BASE_DIR / "localharness_external"

# Maximum characters of file content to attach to prevent overwhelming stdio
MAX_CONTENT_CHARS = 500_000
MAX_CONTENT_LINES = 5_000


def read_file_slice(file_path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> Optional[str]:
    """Reads specific line slice of a file safely from disk."""
    if not file_path or not os.path.isfile(file_path):
        return None
    try:
        # Check size before reading
        if os.path.getsize(file_path) > 20 * 1024 * 1024:
            # For very large files, stream line by line
            lines = []
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                for idx, line in enumerate(f, start=1):
                    if start_line and idx < start_line:
                        continue
                    if end_line and idx > end_line:
                        break
                    lines.append(line)
                    if len(lines) >= MAX_CONTENT_LINES:
                        lines.append("\n... [truncated: file slice exceeds limit] ...\n")
                        break
            return "".join(lines)
        else:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            s_idx = max(0, start_line - 1) if (start_line and start_line > 0) else 0
            e_idx = end_line if (end_line and end_line > 0) else len(all_lines)
            selected = all_lines[s_idx:e_idx]
            if len(selected) > MAX_CONTENT_LINES:
                selected = selected[:MAX_CONTENT_LINES]
                selected.append("\n... [truncated: file slice exceeds limit] ...\n")
            res = "".join(selected)
            if len(res) > MAX_CONTENT_CHARS:
                res = res[:MAX_CONTENT_CHARS] + "\n... [truncated] ...\n"
            return res
    except Exception:
        return None


class AcpBridge:
    def __init__(self, server_args):
        self.server_args = server_args
        self.active_tool_calls: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.proc: Optional[subprocess.Popen] = None

    def start_server(self):
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        if HARNESS_BIN.exists() and not os.access(HARNESS_BIN, os.X_OK):
            try:
                HARNESS_BIN.chmod(0o755)
            except Exception:
                pass

        if LIB_IPV4_SO.exists():
            existing = env.get("LD_PRELOAD", "")
            env["LD_PRELOAD"] = f"{LIB_IPV4_SO}:{existing}" if existing else str(LIB_IPV4_SO)

        cmd = [str(SERVER_BIN)]
        if "--uid=" not in self.server_args:
            cmd.append("--uid=")
        cmd.extend(self.server_args)

        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            text=True,
            bufsize=1,
            env=env,
        )

    def forward_stdin(self):
        """Forwards input from ACP client (Paseo) to agy_acp_server."""
        try:
            for line in iter(sys.stdin.readline, ""):
                if not line:
                    break
                if self.proc and self.proc.stdin:
                    self.proc.stdin.write(line)
                    self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            if self.proc and self.proc.stdin:
                try:
                    self.proc.stdin.close()
                except Exception:
                    pass

    def transform_server_line(self, line: str) -> str:
        """Inspects and enriches server output lines before passing to client."""
        stripped = line.strip()
        if not stripped.startswith("{") or not stripped.endswith("}"):
            return line

        try:
            msg = json.loads(line)
        except Exception:
            return line

        method = msg.get("method")
        if method != "session/update":
            return line

        params = msg.get("params")
        if not isinstance(params, dict):
            return line

        update = params.get("update")
        if not isinstance(update, dict):
            return line

        update_type = update.get("sessionUpdate")

        # -------------------------------------------------------------
        # 1. Tool Call Started: sessionUpdate == "tool_call"
        # -------------------------------------------------------------
        if update_type == "tool_call":
            tool_call_id = update.get("toolCallId")
            raw_input = update.get("rawInput")
            kind = update.get("kind")
            title = update.get("title", "")

            if not isinstance(raw_input, dict):
                raw_input = {}
                update["rawInput"] = raw_input

            locations = update.get("locations") or []
            loc_path = locations[0].get("path") if locations and isinstance(locations[0], dict) else None

            # Detection of tool category
            is_read = (
                kind == "read"
                or "view_file" in title.lower()
                or "read" in title.lower()
                or "AbsolutePath" in raw_input
            )
            is_edit = (
                kind == "edit"
                or "replace_file_content" in title.lower()
                or "edit" in title.lower()
                or "TargetContent" in raw_input
            )
            is_write = (
                kind == "write"
                or "write_to_file" in title.lower()
                or "CodeContent" in raw_input
            )
            is_search = (
                kind == "search"
                or "grep" in title.lower()
                or "find" in title.lower()
                or "list_dir" in title.lower()
                or "DirectoryPath" in raw_input
                or "SearchPath" in raw_input
                or "SearchDirectory" in raw_input
            )
            is_exec = (
                kind == "execute"
                or "run_command" in title.lower()
                or "CommandLine" in raw_input
            )

            file_path = (
                raw_input.get("AbsolutePath")
                or raw_input.get("TargetFile")
                or raw_input.get("filePath")
                or raw_input.get("path")
                or raw_input.get("file")
                or loc_path
            )

            start_line = (
                raw_input.get("StartLine")
                or raw_input.get("startLine")
                or raw_input.get("offset")
                or raw_input.get("line")
            )
            end_line = (
                raw_input.get("EndLine")
                or raw_input.get("endLine")
            )

            if is_read:
                update["kind"] = "read"
                if not update.get("name"):
                    update["name"] = "read"

                if file_path:
                    raw_input["filePath"] = file_path
                    raw_input["path"] = file_path
                    raw_input["file"] = file_path

                if start_line is not None:
                    try:
                        s_int = int(start_line)
                        raw_input["line"] = s_int
                        raw_input["offset"] = s_int
                        if end_line is not None:
                            e_int = int(end_line)
                            raw_input["limit"] = max(1, e_int - s_int + 1)
                    except (ValueError, TypeError):
                        pass

                if file_path and not locations:
                    update["locations"] = [{"path": file_path, "line": start_line or 1}]

                # Remember for completion
                if tool_call_id:
                    with self.lock:
                        self.active_tool_calls[tool_call_id] = {
                            "type": "read",
                            "filePath": file_path,
                            "startLine": start_line,
                            "endLine": end_line,
                        }

            elif is_edit:
                update["kind"] = "edit"
                if not update.get("name"):
                    update["name"] = "edit"
                if file_path:
                    raw_input["filePath"] = file_path
                    raw_input["path"] = file_path
                    raw_input["file"] = file_path

                old_str = raw_input.get("TargetContent") or raw_input.get("oldString") or raw_input.get("oldText")
                new_str = raw_input.get("ReplacementContent") or raw_input.get("newString") or raw_input.get("newText")

                if old_str is not None:
                    raw_input["oldString"] = old_str
                    raw_input["oldText"] = old_str
                if new_str is not None:
                    raw_input["newString"] = new_str
                    raw_input["newText"] = new_str

                if old_str is not None and new_str is not None and "content" not in update:
                    update["content"] = [
                        {
                            "type": "diff",
                            "oldText": old_str,
                            "newText": new_str,
                        }
                    ]

            elif is_write:
                update["kind"] = "edit"
                if not update.get("name"):
                    update["name"] = "write"
                if file_path:
                    raw_input["filePath"] = file_path
                    raw_input["path"] = file_path
                code = raw_input.get("CodeContent") or raw_input.get("content")
                if code is not None:
                    raw_input["newString"] = code
                    raw_input["newText"] = code

            elif is_search:
                update["kind"] = "search"
                if not update.get("name"):
                    update["name"] = "search"
                query = (
                    raw_input.get("Query")
                    or raw_input.get("query")
                    or raw_input.get("Pattern")
                    or raw_input.get("pattern")
                )
                search_dir = (
                    raw_input.get("SearchPath")
                    or raw_input.get("SearchDirectory")
                    or raw_input.get("DirectoryPath")
                    or raw_input.get("directory_path")
                )
                if query:
                    raw_input["query"] = query
                    raw_input["pattern"] = query
                if search_dir:
                    raw_input["path"] = search_dir

            elif is_exec:
                update["kind"] = "execute"
                if not update.get("name"):
                    update["name"] = "execute"
                cmd = raw_input.get("CommandLine") or raw_input.get("command")
                cwd = raw_input.get("Cwd") or raw_input.get("cwd")
                if cmd:
                    raw_input["command"] = cmd
                if cwd:
                    raw_input["cwd"] = cwd

            return json.dumps(msg, ensure_ascii=False)

        # -------------------------------------------------------------
        # 2. Tool Call Finished: sessionUpdate == "tool_call_update"
        # -------------------------------------------------------------
        elif update_type == "tool_call_update":
            tool_call_id = update.get("toolCallId")
            status = update.get("status")

            tool_info = None
            if tool_call_id:
                with self.lock:
                    if status in ("completed", "failed"):
                        tool_info = self.active_tool_calls.pop(tool_call_id, None)
                    else:
                        tool_info = self.active_tool_calls.get(tool_call_id)

            if tool_info and tool_info.get("type") == "read" and status == "completed":
                file_path = tool_info.get("filePath")
                start_l = tool_info.get("startLine")
                end_l = tool_info.get("endLine")

                content = read_file_slice(file_path, start_l, end_l)
                if content is not None:
                    raw_out = update.get("rawOutput")
                    if not isinstance(raw_out, dict):
                        orig_desc = str(raw_out) if raw_out is not None else "Read file completed"
                        raw_out = {
                            "content": content,
                            "text": content,
                            "description": orig_desc,
                        }
                    else:
                        raw_out["content"] = content
                        raw_out["text"] = content

                    update["rawOutput"] = raw_out
                    update["content"] = [
                        {
                            "type": "content",
                            "content": {
                                "type": "text",
                                "text": content,
                            },
                        }
                    ]

            return json.dumps(msg, ensure_ascii=False)

        return line

    def run(self) -> int:
        self.start_server()
        if not self.proc:
            return 1

        t_in = threading.Thread(target=self.forward_stdin, daemon=True)
        t_in.start()

        def handle_signal(sig, _frame):
            if self.proc:
                try:
                    self.proc.terminate()
                except Exception:
                    pass
            sys.exit(0)

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        try:
            for line in iter(self.proc.stdout.readline, ""):
                if not line:
                    break
                transformed = self.transform_server_line(line)
                sys.stdout.write(transformed if transformed.endswith("\n") else transformed + "\n")
                sys.stdout.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            if self.proc:
                try:
                    self.proc.wait(timeout=2)
                except Exception:
                    try:
                        self.proc.kill()
                    except Exception:
                        pass

        return self.proc.returncode if self.proc and self.proc.returncode is not None else 0


def main():
    bridge = AcpBridge(sys.argv[1:])
    sys.exit(bridge.run())


if __name__ == "__main__":
    main()
