# Implementation design

## Scope

Version 0.1 turns one YouTube video with English captions into a locally archived
shadowing PDF. The host assistant is the editor; Python scripts are the deterministic
engine. No model API, separate service, audio download or transcription is involved.

```mermaid
flowchart LR
    A[YouTube URL] --> B[yt-dlp: one caption track]
    B --> C[Timed tokens and conservative overlap removal]
    C --> D[Chunks with context and source hashes]
    D --> E[Host model: punctuation and sentence boundaries]
    E --> F[Exact word sequence and coverage checks]
    F --> G[PDF layout and text/link checks]
    G --> H[Host visual review and local delivery]
```

## Responsibilities

| Component | Responsibility |
| --- | --- |
| `SKILL.md` | Trigger, execution sequence, recovery and user delivery |
| `references/` | Editing rules and JSON contract, loaded when needed |
| `scripts/shadowing.py` | Captions, cache, workflow, settings and publication |
| `scripts/subtitles.py` | JSON3/VTT/SRT, deduplication, chunking and fidelity |
| `scripts/pdf_layout.py` | Searchable A4 layout, source/replay links and file ownership |
| `assets/fonts/` | Bundled, licensed Latin/CJK font; no OS font dependency |
| `tools/` | Development, installation, reproducible font generation and packaging |

The deployable runtime lives entirely inside the skill directory. Codex, Claude
Code and WorkBuddy packages are produced from that same directory. WorkBuddy gets
additional host metadata during packaging; the core instructions stay shared.

## Key decisions

**Readable without rewriting.** The model edits only result JSON. The validator
compares its words in order against timed source tokens, preserving contractions,
quantities, meaningful symbols and repetitions. It derives token ranges itself,
so the model does not have to count words or invent timestamps. This check proves
fidelity to cleaned captions, not fidelity to the audio or punctuation accuracy.

**Conservative deduplication.** Only automatic tracks undergo overlap removal.
Adjacent cues need an overlapping phrase of at least three words and timing/window
evidence. JSON3 append events retain new speech. Where word times are supplied,
repeated positions must agree. Ambiguous cases remain visible rather than deleting
potential speech. Raw captions and a removal record allow inspection.

**Complete and resumable.** Stable source/chunk hashes detect changed inputs.
Caption provenance is bound to extracted sources. Each accepted chunk remains on
disk; `next` finds unfinished or invalid work. Context is read-only and does not
expand a chunk's output range. Cross-boundary sentences can request `join_previous`.
Rendering requires every chunk and repeats validation rather than trusting a flag.

**Useful replay links.** Sentence numbers point to the first source word/cue time,
with a configurable lead of one second by default. No timestamp is printed in the
body. Precision depends on source captions and is never invented by the model.

**Separate learning files and cache.** PDFs go into the system Documents directory
under `YouTube Shadowing/channel/title [videoID].pdf`. Raw captions, chunks, settings
and receipts go into the platform-specific application data directory. An explicit
folder applies to one run unless the user asks to remember it.

**Protect existing files.** A byte hash and receipt identify an owned generated PDF.
Unchanged output can be reused; unknown or modified files receive a versioned
neighbor. Publication checks the staged PDF before writing to the learning folder.
Visual inspection remains the host's responsibility.

## Error boundaries

Missing captions, access restrictions, bot challenges, rate limits and extractor
failures have separate error kinds. Only rate-limit/network failures receive two
short retries. The engine never reads browser cookies or starts an unrequested
audio workflow. Invalid model output gets at most two repair attempts per chunk
under the skill instructions; recovery data is retained if editing stops.

## Release validation

Automated tests cover parser formats, overlap versus real repetitions, fidelity,
long transcripts, resume behavior, provenance, PDF text and links, long paragraphs,
CJK metadata, filename collisions and complete packages. GitHub Actions defines a
Windows/Linux/macOS matrix; the configured jobs must actually run before claiming
cross-platform CI success. Claude Code and WorkBuddy require separate host trials.
