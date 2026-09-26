"""Model providers.

Three paths, deliberately kept apart:

* ``AnthropicProvider`` - the official ``anthropic`` SDK, billed per token.
* ``OpenAIProvider``    - the REST Chat Completions endpoint, billed per token.
* ``CLIProvider``       - a locally installed, subscription-authenticated CLI
  (Claude Code on a Pro/Max plan, Codex on a ChatGPT Plus/Pro plan). No API
  key, no per-token charge: the work draws on the subscription's quota.
"""

import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time

from . import catalog
from .cli import find_cli, command as cli_command


class ProviderError(RuntimeError):
    pass


class MissingCredentials(ProviderError):
    pass


class Cancelled(ProviderError):
    """The task was cancelled while a CLI call was in flight."""


def _kill_tree(proc):
    """Stop a CLI and everything it started.

    On Windows the CLI is a .CMD shim, so proc.kill() would only end cmd.exe
    and leave the real node process running (and spending quota).
    """
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, timeout=15,
                           **no_window_kwargs())
        elif os.getpgid(proc.pid) == proc.pid:
            # Started with start_new_session: take the whole group down.
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except Exception:
        pass
    try:
        proc.communicate(timeout=5)
    except Exception:
        pass


class LLMResult(object):
    """Normalised result of one model call."""

    def __init__(self, text="", tool_calls=None, stop_reason="end_turn",
                 model="", in_tokens=0, out_tokens=0, raw_content=None,
                 refused=False, refusal_category="", thinking="",
                 quota_usd=0.0, session_id="", billing_model=""):
        self.text = text
        self.tool_calls = tool_calls or []
        self.stop_reason = stop_reason
        self.model = model
        self.in_tokens = in_tokens
        self.out_tokens = out_tokens
        self.raw_content = raw_content if raw_content is not None else []
        self.refused = refused
        self.refusal_category = refusal_category
        self.thinking = thinking
        # What the same work would have cost on the API. Reported by the CLIs
        # for information only - a subscription call adds no billable spend.
        self.quota_usd = quota_usd
        # CLI conversation id, so the next turn can resume instead of resend.
        self.session_id = session_id
        # The id that was requested. APIs answer with a dated snapshot id
        # (claude-haiku-4-5-20251001, gpt-4o-mini-2024-07-18) that the
        # catalog does not list, which would price every call at $0.
        self.billing_model = billing_model

    @property
    def cost_usd(self):
        return catalog.cost(self.billing_model or self.model,
                            self.in_tokens, self.out_tokens)

    def __repr__(self):
        return "<LLMResult %s stop=%s tools=%d>" % (
            self.model, self.stop_reason, len(self.tool_calls))


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

class AnthropicProvider(object):
    name = catalog.ANTHROPIC

    def __init__(self, api_key):
        if not api_key:
            raise MissingCredentials(
                "No Anthropic API key. Add one in Settings -> Account, "
                "or set ANTHROPIC_API_KEY.")
        try:
            import anthropic
        except ImportError as exc:      # pragma: no cover
            raise ProviderError(
                "The 'anthropic' package is required: pip install anthropic"
            ) from exc
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key)

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def _build_kwargs(model, messages, system=None, tools=None,
                      max_tokens=8000, effort=None, thinking=True,
                      output_format=None):
        spec = catalog.get(model)
        kwargs = {
            "model": model,
            "max_tokens": int(max_tokens),
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        output_config = {}
        if spec and spec.no_sampling_params:
            # Opus 5.5 / Fable 5.1 / Opus 5 / Sonnet 5 family: adaptive
            # thinking, no temperature/top_p/top_k, no budget_tokens.
            # Models with thinking always on reject {"type": "disabled"}, so
            # the only safe values are adaptive or omitting the field.
            if thinking or spec.thinking_always_on:
                kwargs["thinking"] = {"type": "adaptive",
                                      "display": "summarized"}
            if effort and spec.supports_effort:
                output_config["effort"] = effort
        if output_format:
            output_config["format"] = output_format
        if output_config:
            kwargs["output_config"] = output_config
        return kwargs

    @staticmethod
    def _parse_message(msg):
        text_parts, thinking_parts, tool_calls, raw = [], [], [], []
        for block in getattr(msg, "content", []) or []:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
                raw.append({"type": "text", "text": block.text})
            elif btype == "thinking":
                tval = getattr(block, "thinking", "") or ""
                if tval:
                    thinking_parts.append(tval)
                raw.append(block)
            elif btype == "tool_use":
                tool_calls.append({"id": block.id, "name": block.name,
                                   "input": block.input})
                raw.append(block)
            else:
                raw.append(block)

        usage = getattr(msg, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        # Cached reads are billed but still count as input volume for display.
        in_tok += getattr(usage, "cache_read_input_tokens", 0) or 0
        in_tok += getattr(usage, "cache_creation_input_tokens", 0) or 0

        stop = getattr(msg, "stop_reason", "") or ""
        refused = stop == "refusal"
        category = ""
        details = getattr(msg, "stop_details", None)
        if details is not None:
            category = getattr(details, "category", "") or ""

        return LLMResult(
            text="\n".join(text_parts).strip(),
            tool_calls=tool_calls,
            stop_reason=stop,
            model=getattr(msg, "model", "") or "",
            in_tokens=in_tok,
            out_tokens=out_tok,
            raw_content=raw,
            refused=refused,
            refusal_category=category,
            thinking="\n".join(thinking_parts).strip(),
        )

    # -- public --------------------------------------------------------------
    def complete(self, model, messages, system=None, tools=None,
                 max_tokens=8000, effort=None, thinking=True, timeout=None,
                 output_format=None, workspace=None):
        kwargs = self._build_kwargs(model, messages, system, tools,
                                    max_tokens, effort, thinking,
                                    output_format)
        client = self._client
        if timeout:
            client = self._client.with_options(timeout=float(timeout))
        try:
            if int(max_tokens) > 16000:
                # Large outputs must stream or the HTTP request can time out.
                with client.messages.stream(**kwargs) as stream:
                    msg = stream.get_final_message()
            else:
                msg = client.messages.create(**kwargs)
        except self._anthropic.APIStatusError as exc:
            raise ProviderError("Anthropic API error %s: %s"
                                % (exc.status_code, exc.message)) from exc
        except self._anthropic.APIConnectionError as exc:
            raise ProviderError("Could not reach the Anthropic API: %s"
                                % exc) from exc
        result = self._parse_message(msg)
        result.billing_model = model
        return result

    def list_models(self, timeout=30):
        """Live model ids straight from the Models API."""
        try:
            return sorted(m.id for m in self._client.models.list())
        except self._anthropic.APIStatusError as exc:
            raise ProviderError("Anthropic API error %s: %s"
                                % (exc.status_code, exc.message)) from exc
        except Exception as exc:
            raise ProviderError("Could not list Anthropic models: %s" % exc)


# ---------------------------------------------------------------------------
# OpenAI (REST)
# ---------------------------------------------------------------------------

_REASONING_PREFIXES = ("o1", "o3", "o4", "o5")


def _chat_messages(messages):
    """Translate the engine's Anthropic-shaped transcript to Chat Completions."""
    out = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str) or content is None:
            out.append(dict(message))
            continue
        texts, calls, results = [], [], []
        for block in content:
            if not isinstance(block, dict):
                block = block.model_dump() if hasattr(block, "model_dump") else {}
            kind = block.get("type")
            if kind == "text":
                texts.append(block.get("text", ""))
            elif kind == "tool_use":
                calls.append({"id": block["id"], "type": "function",
                              "function": {"name": block["name"],
                                           "arguments": json.dumps(block.get("input") or {})}})
            elif kind == "tool_result":
                result = block.get("content", "")
                results.append({"role": "tool", "tool_call_id": block["tool_use_id"],
                                "content": result if isinstance(result, str) else json.dumps(result)})
        if texts or calls:
            row = {"role": message["role"], "content": "\n".join(texts) or None}
            if calls:
                row["tool_calls"] = calls
            out.append(row)
        out.extend(results)
    return out


class OpenAICompatibleProvider(object):
    """One client for every vendor speaking the OpenAI Chat Completions shape.

    Covers OpenAI itself plus Google Gemini, Moonshot (Kimi) and Alibaba
    (Qwen), which all publish OpenAI-compatible endpoints.
    """

    def __init__(self, provider_id, api_key, base_url):
        info = catalog.PROVIDERS.get(provider_id)
        self.name = provider_id
        self.label = info.label if info else provider_id
        if not api_key:
            raise MissingCredentials(
                "No %s API key. Add one in Settings -> Account, or set %s."
                % (self.label, (info.env_var if info else "the API key")))
        self.api_key = api_key
        self.base_url = (base_url or (info.base_url if info else "")).rstrip("/")
        if not self.base_url:
            raise ProviderError("No base URL configured for %s." % self.label)

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def _is_reasoning(model):
        return (model.split("-")[0] in _REASONING_PREFIXES
                or model.startswith(("gpt-5", "gpt-6")))

    def _headers(self):
        return {"Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json"}

    def _error(self, resp):
        detail = resp.text
        try:
            body = resp.json()
            detail = (body.get("error") or {}).get("message") or detail
        except Exception:
            pass
        return ProviderError("%s API error %s: %s"
                             % (self.label, resp.status_code,
                                str(detail)[:500]))

    # -- public --------------------------------------------------------------
    def complete(self, model, messages, system=None, tools=None,
                 max_tokens=4000, effort=None, thinking=True, timeout=120,
                 output_format=None, workspace=None):
        import requests

        payload_messages = []
        if system:
            role = "developer" if self._is_reasoning(model) else "system"
            payload_messages.append({"role": role, "content": system})
        payload_messages.extend(_chat_messages(messages))

        payload = {"model": model, "messages": payload_messages}
        if tools:
            payload["tools"] = [
                {"type": "function", "function": {
                    "name": tool["name"], "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object"})}}
                for tool in tools]
        if self._is_reasoning(model):
            payload["max_completion_tokens"] = int(max_tokens)
            if effort in ("low", "medium", "high"):
                payload["reasoning_effort"] = effort
        else:
            payload["max_tokens"] = int(max_tokens)
        if output_format and output_format.get("schema"):
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "strict": True,
                                "schema": output_format["schema"]},
            }

        try:
            resp = requests.post(
                self.base_url + "/chat/completions",
                headers=self._headers(), data=json.dumps(payload),
                timeout=float(timeout or 120))
        except Exception as exc:
            raise ProviderError("Could not reach %s: %s" % (self.label, exc))

        if resp.status_code >= 400:
            raise self._error(resp)

        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        text = (message.get("content") or "")
        if isinstance(text, list):      # some vendors return content parts
            text = "".join(p.get("text", "") for p in text
                           if isinstance(p, dict))
        usage = data.get("usage") or {}
        calls, raw = [], []
        if text:
            raw.append({"type": "text", "text": text})
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            try:
                arguments = json.loads(function.get("arguments") or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("arguments must be an object")
            except (ValueError, TypeError) as exc:
                raise ProviderError("Invalid tool arguments from %s: %s" % (self.label, exc))
            calls.append({"id": call["id"], "name": function["name"], "input": arguments})
            raw.append({"type": "tool_use", **calls[-1]})
        finish = choice.get("finish_reason") or "stop"
        return LLMResult(
            text=(text or "").strip(),
            tool_calls=calls, raw_content=raw,
            refused=bool(message.get("refusal")) or finish == "content_filter",
            stop_reason="max_tokens" if finish == "length" else finish,
            model=data.get("model") or model,
            billing_model=model,
            in_tokens=int(usage.get("prompt_tokens") or 0),
            out_tokens=int(usage.get("completion_tokens") or 0),
        )

    def list_models(self, timeout=30):
        """GET /models - the vendor's own view of what it serves."""
        import requests
        try:
            resp = requests.get(self.base_url + "/models",
                                headers=self._headers(),
                                timeout=float(timeout))
        except Exception as exc:
            raise ProviderError("Could not reach %s: %s" % (self.label, exc))
        if resp.status_code >= 400:
            raise self._error(resp)
        body = resp.json()
        rows = body.get("data") if isinstance(body, dict) else body
        out = []
        for row in rows or []:
            if isinstance(row, str):
                out.append(row)
            elif isinstance(row, dict) and row.get("id"):
                out.append(row["id"])
        return sorted(set(out))


# Kept for callers that still reference the old name.
def OpenAIProvider(api_key):
    return OpenAICompatibleProvider(catalog.OPENAI, api_key,
                                    catalog.PROVIDERS[catalog.OPENAI].base_url)


# ---------------------------------------------------------------------------
# Subscription CLIs (Claude Pro/Max, ChatGPT Plus/Pro)
# ---------------------------------------------------------------------------

def no_window_kwargs():
    """Keep Windows from flashing a console window for each subprocess.

    The agent CLIs are .CMD shims, so without this every call pops a terminal
    in front of whatever the user is doing.
    """
    if os.name != "nt":
        return {}
    kwargs = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    try:
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = 0          # SW_HIDE
        kwargs["startupinfo"] = info
    except Exception:
        pass
    return kwargs


class CLIProvider(object):
    """Drives a locally installed, already-logged-in agent CLI.

    The CLI carries its own credentials and its own tools, so a call here is a
    whole delegated turn rather than one step of our tool loop.
    """

    name = catalog.CLI

    def __init__(self, spec, settings):
        if not spec or not spec.cli_bin:
            raise ProviderError("Not a CLI-backed model.")
        self.spec = spec
        self.settings = settings
        self.binary = find_cli(spec.cli_bin)
        if not self.binary:
            raise MissingCredentials(
                "%s is not on PATH. Install it and sign in with your "
                "subscription, then reopen CollaboratorMCP."
                % spec.cli_bin)

    # -- helpers -------------------------------------------------------------
    def _extra_args(self, key):
        raw = self.settings.get(key) or ""
        if not raw.strip():
            return []
        try:
            return [part[1:-1] if len(part) >= 2 and part[0] == part[-1]
                    and part[0] in ("'", '"') else part
                    for part in shlex.split(raw, posix=False)]
        except Exception:
            return raw.split()

    # Set by the engine so CLI activity is visible in-app instead of in the
    # console windows we deliberately hide.
    console_hook = None

    def _console(self, kind, text):
        hook = CLIProvider.console_hook
        if hook is None or not text:
            return
        try:
            hook(kind, text)
        except Exception:
            pass

    def _run(self, argv, cwd, timeout, stdin_text=None, should_stop=None):
        argv = cli_command(argv[0]) + argv[1:]
        # The prompt always goes over stdin, never as an argv element: these
        # CLIs are .CMD shims on Windows, and a multi-line argument gets
        # truncated at the first newline by the command processor.
        printable = " ".join(
            ('"%s"' % a if " " in a else a)
            for a in ([os.path.basename(argv[0])] + argv[1:]))
        self._console("cmd", "%s  (cwd %s)" % (printable, cwd))
        if stdin_text:
            self._console("stdin", stdin_text)
        started = time.time()
        deadline = started + float(timeout)
        try:
            proc = subprocess.Popen(
                argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace", shell=False, **no_window_kwargs())
        except OSError as exc:
            self._console("err", "could not run: %s" % exc)
            raise ProviderError("Could not run %s: %s"
                                % (self.spec.cli_bin, exc))

        # Poll in short slices so a cancelled task stops burning plan quota
        # now, not when the CLI eventually finishes on its own.
        pending_input = stdin_text if stdin_text is not None else ""
        while True:
            try:
                stdout, stderr = proc.communicate(input=pending_input,
                                                  timeout=1.0)
                break
            except subprocess.TimeoutExpired:
                pending_input = None     # already handed to the process
                if should_stop is not None and should_stop():
                    _kill_tree(proc)
                    self._console("err", "stopped: task cancelled")
                    raise Cancelled("%s stopped: task cancelled."
                                    % self.spec.cli_bin)
                if time.time() >= deadline:
                    _kill_tree(proc)
                    self._console("err", "timed out after %ss" % int(timeout))
                    raise ProviderError("%s timed out after %ss."
                                        % (self.spec.cli_bin, int(timeout)))
        self._console("out", stdout or "")
        if stderr:
            self._console("err", stderr)
        self._console("exit", "exit %d in %.1fs"
                      % (proc.returncode, time.time() - started))
        return subprocess.CompletedProcess(argv, proc.returncode,
                                           stdout or "", stderr or "")

    # -- public --------------------------------------------------------------
    @property
    def supports_resume(self):
        """Whether a follow-up turn can continue a session instead of
        resending the whole transcript."""
        return self.spec.cli_kind == "claude"

    def complete(self, model, messages, system=None, tools=None,
                 max_tokens=8000, effort=None, thinking=True, timeout=None,
                 output_format=None, workspace=None, should_stop=None,
                 resume=None):
        prompt = _flatten_messages(messages)
        if output_format:
            prompt += ("\n\nReply with a single JSON object matching this "
                       "schema and nothing else - no prose, no code fence:\n"
                       + json.dumps(output_format.get("schema") or {}))
        workspace = workspace or self.settings.workspace_dir()
        timeout = float(timeout or self.settings.get("cli_timeout_s") or 900)

        if self.spec.cli_kind == "claude":
            return self._run_claude(prompt, system, workspace, timeout, effort,
                                    should_stop, resume)
        return self._run_codex(prompt, system, workspace, timeout,
                               should_stop, output_format)

    # Claude Code is an agent harness: left alone it reaches for its own
    # tools, hits its non-interactive permission prompts and gives up. We want
    # it as a text engine only - CollaboratorMCP runs the tools itself, behind
    # the same approval gate and workspace confinement as API mode - so every
    # built-in tool is denied up front.
    CLAUDE_DENIED_TOOLS = ("Bash,Read,Write,Edit,NotebookEdit,Glob,Grep,"
                           "Task,WebFetch,WebSearch,TodoWrite")

    def _run_claude(self, prompt, system, workspace, timeout, effort=None,
                    should_stop=None, resume=None):
        extra = self._extra_args("cli_claude_args")
        argv = [self.binary, "-p", "--output-format", "json"]
        if resume:
            # Continue the same conversation: only the new turn goes over
            # stdin, instead of the whole growing transcript.
            argv += ["--resume", resume]
        if self.spec.cli_model:
            argv += ["--model", self.spec.cli_model]
        if system:
            argv += ["--append-system-prompt", system]
        if "--disallowed-tools" not in " ".join(extra) and \
                "--disallowedTools" not in " ".join(extra):
            argv += ["--disallowed-tools", self.CLAUDE_DENIED_TOOLS]
        if effort in ("low", "medium", "high", "xhigh", "max"):
            argv += ["--effort", effort]
        argv += extra

        proc = self._run(argv, workspace, timeout, stdin_text=prompt,
                         should_stop=should_stop)
        stdout = (proc.stdout or "").strip()
        payload = _first_json_object(stdout)

        if payload is None:
            # Not JSON - older CLI, or a plain-text error. Treat a clean exit
            # as a plain-text answer rather than losing the work.
            if proc.returncode == 0 and stdout:
                return LLMResult(text=stdout, stop_reason="end_turn",
                                 model=self.spec.id)
            detail = (proc.stderr or stdout).strip()[-1200:]
            raise ProviderError(
                "Claude Code returned no usable output (exit %d).%s"
                % (proc.returncode, ("\n" + detail) if detail else ""))

        text = payload.get("result") or ""
        if proc.returncode != 0 or payload.get("is_error"):
            detail = text or "; ".join(str(e) for e in payload.get("errors", []))
            raise ProviderError("Claude Code failed (exit %d): %s" % (
                proc.returncode, detail or (proc.stderr or stdout)[-1200:]))
        usage = payload.get("usage") or {}
        in_tok = (int(usage.get("input_tokens") or 0)
                  + int(usage.get("cache_read_input_tokens") or 0)
                  + int(usage.get("cache_creation_input_tokens") or 0))
        out_tok = int(usage.get("output_tokens") or 0)
        subtype = payload.get("subtype") or ""
        return LLMResult(
            text=text,
            stop_reason="error" if payload.get("is_error") else (
                payload.get("stop_reason") or "end_turn"),
            model=self.spec.id,
            in_tokens=in_tok,
            out_tokens=out_tok,
            refused=subtype == "refusal",
            quota_usd=float(payload.get("total_cost_usd") or 0.0),
            session_id=payload.get("session_id") or "",
        )

    def _run_codex(self, prompt, system, workspace, timeout, should_stop=None, output_format=None):
        if system:
            prompt = system + "\n\n---\n\n" + prompt
        handle, out_path = tempfile.mkstemp(prefix="codex-", suffix=".txt")
        os.close(handle)
        schema_path = None
        # Text engine only - CollaboratorMCP runs the tools, so the sandbox is
        # set read-only by default and Codex never edits on its own.
        argv = [self.binary, "exec",
                "--cd", workspace,
                "--skip-git-repo-check",
                "--sandbox", self.settings.get("cli_codex_sandbox")
                or "read-only",
                "--output-last-message", out_path]
        if self.spec.cli_model:
            argv += ["--model", self.spec.cli_model]
        # A local planner/helper must not connect back to its own host as an
        # external orchestrator. This also avoids recursive MCP startups.
        codex_home = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
        try:
            with open(os.path.join(codex_home, "config.toml"), encoding="utf-8") as config_file:
                registered = "[mcp_servers.collaborator]" in config_file.read()
        except OSError:
            registered = False
        if registered:
            argv += ["-c", "mcp_servers.collaborator.enabled=false"]
        argv += self._extra_args("cli_codex_args")
        argv.append("-")          # read the prompt from stdin

        try:
            if output_format and output_format.get("schema"):
                handle, schema_path = tempfile.mkstemp(prefix="codex-schema-", suffix=".json")
                with os.fdopen(handle, "w", encoding="utf-8") as schema_file:
                    json.dump(output_format["schema"], schema_file)
                argv[-1:-1] = ["--output-schema", schema_path]
            proc = self._run(argv, workspace, timeout, stdin_text=prompt,
                             should_stop=should_stop)
            text = ""
            try:
                with open(out_path, "r", encoding="utf-8",
                          errors="replace") as fh:
                    text = fh.read().strip()
            except OSError:
                text = ""
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip()[-1200:]
                raise ProviderError("Codex CLI failed (exit %d).%s"
                                    % (proc.returncode,
                                       ("\n" + detail) if detail else ""))
            if not text:
                raise ProviderError("Codex CLI returned no final answer. "
                                    "Check Codex sign-in and the Terminals page.")
            return LLMResult(text=text,
                             stop_reason="end_turn", model=self.spec.id)
        finally:
            for path in (out_path, schema_path):
                if path:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass


def _first_json_object(text):
    """Find a JSON object in CLI output, whether it is one line or many."""
    if not text:
        return None
    candidates = []
    stripped = text.strip()
    if stripped.startswith("{"):
        candidates.append(stripped)
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            candidates.append(line)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start:end + 1])
    for chunk in candidates:
        try:
            parsed = json.loads(chunk)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _flatten_messages(messages):
    """Collapse a message list into a single prompt for a CLI."""
    parts = []
    for msg in messages or []:
        role = msg.get("role", "user")
        content = msg.get("content")
        if isinstance(content, str):
            body = content
        else:
            chunks = []
            for block in content or []:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        chunks.append(block.get("text") or "")
                    elif block.get("type") == "tool_result":
                        chunks.append(str(block.get("content") or ""))
                else:
                    chunks.append(str(getattr(block, "text", "") or ""))
            body = "\n".join(c for c in chunks if c)
        if not body:
            continue
        parts.append(body if role == "user" else "%s: %s" % (role, body))
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def provider_key(settings, provider_id):
    """The API key for a provider: stored value, else its environment var."""
    info = catalog.PROVIDERS.get(provider_id)
    if not info or not info.key_setting:
        return ""
    value = (settings.get(info.key_setting) or "").strip()
    if not value and info.env_var:
        value = (os.environ.get(info.env_var) or "").strip()
    if not value and provider_id == catalog.GOOGLE:
        value = (os.environ.get("GOOGLE_API_KEY") or "").strip()
    return value


def provider_base_url(settings, provider_id):
    info = catalog.PROVIDERS.get(provider_id)
    if not info:
        return ""
    if info.base_url_setting:
        override = (settings.get(info.base_url_setting) or "").strip()
        if override:
            return override
    return info.base_url


def provider_for(model_id, settings):
    """Return a provider instance able to serve ``model_id``."""
    spec = catalog.get(model_id)
    provider_id = spec.provider if spec else catalog.provider_of(model_id)
    if provider_id == catalog.CLI:
        return CLIProvider(spec, settings)
    if provider_id == catalog.ANTHROPIC:
        return AnthropicProvider(provider_key(settings, catalog.ANTHROPIC))
    return OpenAICompatibleProvider(provider_id,
                                    provider_key(settings, provider_id),
                                    provider_base_url(settings, provider_id))


def discovery_provider(settings, provider_id):
    """A client usable only for listing models."""
    if provider_id == catalog.CLI:
        return None
    if provider_id == catalog.ANTHROPIC:
        return AnthropicProvider(provider_key(settings, catalog.ANTHROPIC))
    return OpenAICompatibleProvider(provider_id,
                                    provider_key(settings, provider_id),
                                    provider_base_url(settings, provider_id))


def credentials_status(settings):
    """What this install can actually talk to right now."""
    status = {pid: bool(provider_key(settings, pid))
              for pid in catalog.API_PROVIDERS}
    status["claude_cli"] = find_cli("claude") is not None
    status["codex_cli"] = find_cli("codex") is not None
    return status


def configured_providers(settings):
    """Provider ids that currently have a usable credential."""
    return [pid for pid in catalog.API_PROVIDERS
            if provider_key(settings, pid)]


def check_model_ready(model_id, settings):
    """(ready, message) for a configured model - used by the UI."""
    spec = catalog.get(model_id)
    provider_id = spec.provider if spec else catalog.provider_of(model_id)
    if provider_id == catalog.CLI:
        binary = spec.cli_bin if spec else ""
        if binary and find_cli(binary):
            return (True, "%s found on PATH" % binary)
        return (False, "%s is not installed or not on PATH"
                % (binary or "the CLI"))
    info = catalog.PROVIDERS.get(provider_id)
    label = info.label if info else provider_id
    if provider_key(settings, provider_id):
        return (True, "%s API key set" % label)
    return (False, "No %s API key" % label)
