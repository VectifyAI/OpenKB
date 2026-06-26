# Chat TUI

`openkb chat` is an interactive REPL over your wiki. Unlike `query` (one-shot), a
chat session keeps context across turns, can edit the KB through slash commands,
and is saved so you can resume it later.

```bash
openkb chat
```

```text
OpenKB Chat
~/research-kb · anthropic/claude-sonnet-4-6 · session 20260625-143022-a1x
Type /help for commands, Ctrl-D to exit, Ctrl-C to abort the current response.

>>> How do the two papers differ on their use of attention?
Both rely on scaled dot-product attention, but…
  · read_wiki_file(path="concepts/self-attention.md")
  · read_wiki_file(path="summaries/deepseek-r1.md")

>>> /save attention-comparison
Saved to wiki/explorations/attention-comparison-20260625.md
```

Answers are grounded in your wiki: the agent reads `concepts/`, `summaries/`,
`entities/`, and source files, and shows the tool calls it makes. Responses render
as rich Markdown (headings, tables, code) in a terminal.

---

## Persistent sessions

Every conversation is stored as JSON in `<kb>/.openkb/chats/`. Manage them with:

```bash
openkb chat --list              # table of sessions: id · turns · updated · title
openkb chat --resume            # resume the most recent session
openkb chat --resume 20260625   # resume by id or unique prefix
openkb chat --delete 20260625   # delete a session
```

Resuming replays the last few turns so you have context:

```text
$ openkb chat --resume
Resumed session · 4 turn(s)
[3] >>> How do the two papers differ on their use of attention?
[3]     Both rely on scaled dot-product attention, but…
[4] >>> /save attention-comparison
```

---

## Slash commands

Inside the REPL, lines starting with `/` are commands rather than questions. Run
`/help` to see the current set; the built-ins are:

| Command | What it does |
| --- | --- |
| `/help` | List available commands |
| `/exit`, `/quit` | Leave the REPL (Ctrl-D also works) |
| `/clear` | Start a fresh session (the previous one is saved) |
| `/save [name]` | Export the transcript to `wiki/explorations/<name>-<date>.md` |
| `/status` | Show KB status without leaving chat |
| `/list` | List documents in the KB |
| `/lint` | Run the integrity + knowledge lint |
| `/add <path>` | Ingest a file or directory (Tab-completes paths) |
| `/skill new <name> "<intent>"` | Compile a skill from the wiki — see [`skills/`](../skills/) |
| `/deck new [--critique] [--skill <name>] <name> "<intent>"` | Generate an HTML deck — see [`slides/`](../slides/) |
| `/critique <path>` | Run the HTML critic over an existing deck/page |

Slash commands run inline — errors are reported and the conversation continues;
Ctrl-C aborts the running command without ending the session.

### Why slash commands matter

They turn chat into a workbench: ask a question, realize you're missing a source,
`/add` it, and keep going — all in one session. Because the chat agent can write
to `wiki/explorations/**` and `output/**`, asking it to "write that up as a note"
or "turn this into a skill" produces real files you keep.

---

## Plain output for piping or logs

```bash
openkb chat --no-color     # disable colored output entirely
openkb chat --raw          # show raw Markdown source, keep prompt/tool colors
```

`--no-color` also respects the `NO_COLOR` environment variable.
