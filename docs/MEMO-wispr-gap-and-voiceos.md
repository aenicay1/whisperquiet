# MEMO: Distance to Wispr Flow, distance to a VoiceOS

**Date:** 2026-07-02
**Purpose:** Answer two strategic questions with evidence: (1) how far is
WhisperQuiet from Wispr Flow tech-wise, and (2) how far is it from a credible
"VoiceOS" (voice as a system-wide input *and control* layer). Sources: this
repo's docs/code/benchmarks, and a web research pass on Wispr's public
materials (wisprflow.ai, docs.wisprflow.ai, funding PR, press) as of mid-2026.

---

## TL;DR

- **Core dictation loop: rough parity today.** WhisperQuiet matches Wispr's
  hotkey → transcribe → clean → inject loop and structurally beats them on the
  one thing they cannot copy: **Wispr has no on-device or offline mode at any
  tier, even Enterprise** — their "Privacy Mode" changes retention, not where
  audio is processed.
- **The real gap to Wispr is context awareness** (app-aware tone/formatting,
  their headline feature). Closing it on-device is 6–12 months with genuine
  research risk (our one small-LLM experiment measurably failed and was
  benched — see rescore verdict below).
- **VoiceOS: we are at essentially zero on the action layer — but so is
  Wispr.** Their "Actions" is announced roadmap, not shipped product, despite
  $81M raised and ~50–95 staff. The decisive next input is the **AX census**
  (`scripts/ax_census.py`, committed but never run): it answers whether a
  local agent can drive the apps we actually use.
- **Platform threat to everyone:** Apple's macOS 26/27 on-device
  SpeechAnalyzer dictation is commoditizing plain transcription for free.
  Pure dictation quality is a melting moat; "private input + private action"
  is the position nobody occupies yet.

---

## 1. Wispr Flow, mid-2026 snapshot

Facts from primary sources unless flagged; several aggregator claims are
explicitly marked unverified.

- **Product:** Mac, Windows, iOS, Android ("only major AI dictation tool on
  all four platforms"). 100+ languages, all tiers. Auto-formatting
  (punctuation, casing, filler stripping). Personal dictionary that
  auto-learns. Whisper-mode support. Scratchpad notes. Teams/Enterprise
  (SOC 2 II, ISO 27001, HIPAA, SSO).
- **Context awareness (headline feature):** reads the active app and adapts —
  casual in Slack, professional in Gmail, technical in code. Sends nearby
  text, on-screen proper nouns, app metadata, and (in editors) variable/file
  names to their backend. Native Cursor/Windsurf/VS Code integrations with
  file tagging and variable recognition.
- **Command Mode:** live but labeled Experimental, Pro-only. Text-in/text-out
  transforms on selected text ("make this more formal", "bullet points",
  "translate to Spanish").
- **Claims:** "90% of dictated content requires no edits" (their PR).
  ~700ms p99 transcription+formatting target, 1–2s real-world round trip
  (secondary sources, unverified). ~96–97% accuracy in good conditions,
  ~88% noisy (aggregator-sourced, unverified).
- **Stack:** cloud-only. No offline/on-device mode at any price. Reported
  subprocessors incl. OpenAI/Anthropic/Baseten/Cerebras (unconfirmed).
- **Pricing:** free tier (2,000 words/week desktop), Pro $15/mo ($12 annual).
- **Momentum:** $81M raised through a Nov 2025 $25M extension at $700M
  post-money; Bloomberg (May 2026) reports talks for ~$260M at ~$2B —
  **not confirmed closed**. Founding year, team size, and revenue are all
  inconsistently reported; no official user/ARR disclosures.
- **"Voice OS" vision — their words, published:** "The Master Plan"
  (wisprflow.ai blog, 2026-03-03), three phases:
  1. *Reliable voice input* — drive toward zero edit rate via context+memory
     ("correct a mistake twice, never again").
  2. *Voice to Action* — commands that DO things, first-party actions plus a
     third-party developer ecosystem of voice actions. Vehicle: "Wispr
     Actions", announced ~Dec 2025 as the 2026 focus. **Not shipped at
     production scale as of this memo.**
  3. *Ubiquity via wearables* — rings/watches/glasses, sub-vocalization.
  They explicitly frame themselves as model-agnostic, an interface layer
  above the model providers, positioned against Apple/Google treating voice
  as a feature.

### Adjacent players (one line each)

- **Willow Voice** — near-clone of Wispr on features and price (app-aware
  tone, whisper mode, $15/$12); Mac/Windows/iOS; cited customers Uber, Heidi
  Health. The app-aware-tone space is getting crowded.
- **Superwhisper** — the serious on-device competitor: local Whisper +
  Parakeet on Apple Silicon, free tier, $249 lifetime; positioned for
  privacy-sensitive users. Our closest like-for-like neighbor.
- **Aqua Voice** — cloud, proprietary model tuned for technical/coding
  vocabulary; developer-specialist positioning.
- **MacWhisper** — local file-based transcription (meetings), not live
  dictation; same local-first one-time-pay ethos.
- **Apple** — macOS 26 shipped SpeechAnalyzer (accurate on-device dictation,
  new API); macOS 27 previews systemwide dictation upgrades. Gated to
  M3+/12GB for the best models. The long-term commoditizer of plain
  dictation for every paid product including Wispr.

---

## 2. Where WhisperQuiet stands (honest inventory)

Numbers from `results/baseline.json`, `docs/BACKLOG.md`, and bench scripts.

**Shipped and solid:**
- Push-to-talk loop with streaming preview; the preview is literally the
  committed text (segments lock once; only the tail re-decodes) — constant
  release latency regardless of dictation length.
- whisper-large-v3-turbo (MLX) + personal vocabulary biasing.
  **WER: quiet 2.0% (target ≤5% — passed), whispered 7.1% turbo / 5.1% full
  model. Noisy-condition WER: not yet measured ("pending").**
- Wispr-Flow-style cleanup layer (fillers, self-corrections, "new
  line"/"new paragraph", time formats) — pure rules, zero latency.
- Reliability hardening with a mature decision record (CoreAudio hot-swap
  fixes, native-rate mic open, watchdogs; auto-relaunch backstop
  adversarially reviewed and rejected).
- Resilience fixes shipped 2026-07-01 (download-failure recovery, inject
  guards, decode-crash recovery, flag-key debounce).

**Shipped but rough:**
- Vocabulary is manual (`wq-vocab add`) and needs an app restart.
- Latency instrumentation exists (commit p50/p95 vs <1s target) but **no
  dogfooded numbers are recorded** — BACKLOG's own TODO.
- Packaging: ad-hoc signed friends preview; Developer ID + notarization is
  the public-launch gate.

**Experimental / benched, with measured verdicts:**
- **LLM polish (Qwen2.5-1.5B, `rescore.py`): KEEP OFF.** Latency fine
  (p50 490ms), but over-edited on ~1/3 of changes on 40 real dictations.
  Relevant ceiling for any on-device tone/formatting ambition.
- **Parakeet backend: rejected as default.** ~2x faster but loses on WER and
  has no vocab biasing in the MLX port; left wired for re-eval.
- **Camera/gesture stack: frozen (Pivot #2).** Fully built (winks, brow,
  jaw-drag, head cursor, calibration wizard, HUD), excluded from the preview
  build. A second input modality nobody else has, on ice by explicit
  decision.

**Not built (backlog/aspiration only):**
- LoRA fine-tune on own voice (the structural WER win; corpus is already
  accumulating via keep_audio/keep_transcripts, but no training pipeline
  exists). Log's specced experiment: 30–60 min whispered audio → MLX LoRA →
  measure WER vs stock.
- Auto-vocabulary harvesting from observed edits.
- Any cross-app context awareness, tone adaptation, or command grammar
  beyond the two newline cues.

---

## 3. Gap analysis: WhisperQuiet vs Wispr Flow

**At parity or ahead:** core loop; whispered speech (measured, tuned — their
whisper support exists but this is our specialty); cleanup; privacy
(on-device vs cloud-only — durable and structural); price (free).

**Behind, ordered by difficulty to close:**

1. **Context awareness — the real gap (6–12 months, research risk).**
   They adapt output per focused app using frontier cloud models; we have
   nothing, and our small-model polish experiment failed measurably. Closing
   it on-device means a 3–8B-class local model via MLX plus reading the
   focused app's AX context. Reusable pieces: AX-tree reading
   (`scripts/ax_census.py`), the rescore safety harness (length guards,
   fallback-to-original). Fallback if small local models stay too clumsy:
   an optional BYOK cloud tier — honest, but dilutes the pitch.
2. **Auto-learning dictionary — weeks.** The edit-detection signal
   (`dictation_edited`, transcripts.jsonl) already exists; harvest it
   (consent-gated) instead of manual adds, and hot-reload vocab without
   restart.
3. **Proof of parity — days-to-weeks, currently a hole.** Populate dogfood
   latency p50/p95 and record noisy/whispered WER benchmarks. We cannot
   claim parity we haven't measured.
4. **Breadth — deliberately not our fight.** 100+ languages, 4 platforms,
   enterprise compliance. A one-person on-device product should not compete
   here.

**Verdict:** ~80–90% of Wispr's daily-driver value today for an
English-speaking Mac user; the missing slice is concentrated in context
awareness. Their weakness is our whole thesis: no local mode, ever, and
increasing pressure from Apple below and Willow beside them.

---

## 4. Gap analysis: distance to a VoiceOS

Measured against Wispr's own three phases:

- **Phase 1 (reliable input): competitive.** Quiet WER passes the design
  bar; the LoRA flywheel is their "never correct twice" idea done privately —
  designed, corpus accumulating, pipeline unbuilt.
- **Phase 2 (voice → action): essentially zero built.** No intent parser, no
  command grammar (two regex newline cues), no AX action invocation, no
  AppleScript, no cross-app awareness. Command/dictation disambiguation is
  an open problem in DESIGN.md's deferred list. Existing assets: text/key
  injection primitives, the frozen gesture engine (a second modality), and
  `scripts/ax_census.py` — a read-only feasibility probe of the exact
  make-or-break question, **never run**.
- **Phase 3 (wearables): out of scope.**

**The equalizer:** Wispr hasn't shipped Phase 2 either. Command Mode is
experimental text transformation; Actions is roadmap. And the modern
shortcut — an LLM doing intent parsing over MCP-style tool definitions —
didn't exist when they started. The action layer is unclaimed territory.

**Distance estimate:** a credible VoiceOS v1 — voice actions reliably
driving one's own 5–10 daily apps, locally — is on the order of 6–12 months
of focused work on this codebase, *contingent on the AX census coming back
favorable*. A general-purpose VoiceOS is a company-scale bet (that is what
Wispr's reported $260M raise is for).

**Why "local action" is the right read:** Apple commoditizes dictation from
below; Wispr/Willow own cloud context-awareness. The unoccupied position is
**private input + private action** — an agent that hears you and drives your
apps without anything leaving the machine. That is exactly the moat
`ax_census.py`'s docstring frames ("the agent's local-by-default moat").

### The AX census, explained

macOS's Accessibility (AX) API exposes each app's UI as a tree of elements
with labels and programmatic actions (`AXPress`). An app with a rich labeled
tree can be driven locally by an agent — no vision model, no cloud, no mouse
simulation. An app with an empty tree (many Electron apps) requires
screen-capture + vision, i.e. heavy on-device or cloud — forfeiting the moat.
`scripts/ax_census.py` walks every running app's tree, read-only, and buckets
each into FULL (Tier-1 drivable) / PARTIAL / EMPTY. Run it with the real
daily apps open: if FULL dominates, local-by-default VoiceOS is buildable;
if EMPTY dominates, VoiceOS quietly becomes a cloud/vision product and the
strategy must change. One afternoon; requires Accessibility permission for
the terminal that runs it.

---

## 5. Recommended sequence

1. **Run the AX census** on the real daily app set. Decision input for the
   entire VoiceOS question. (One afternoon.)
2. **Populate the missing numbers:** dogfood latency p50/p95; record fresh
   quiet/noisy/whispered WER references. Gates every parity claim. (Days.)
3. **Run the LoRA experiment** already specced in WHISPERFLOW_AGENT_LOG
   (30–60 min own whispered audio → MLX fine-tune → WER vs stock). The
   structural, privately-compounding WER win. (1–2 weeks.)
4. **Ship auto-vocab harvesting** + hot-reload. Cheap, visible Wispr-gap
   close. (Weeks.)
5. **Then decide context-awareness architecture** (bigger local model vs
   BYOK-optional), informed by 1–4.

Public-launch gate unchanged and orthogonal: Apple Developer ID +
notarization (docs/PREVIEW_TODO.md).

---

*Source caveats: Wispr accuracy/latency figures and subprocessor list are
secondary-sourced and unverified; their Series B ($2B) is reported, not
closed; founding year and team size conflict across sources. Repo claims are
cited from docs/BACKLOG.md, DESIGN.md, results/baseline.json, and code as of
commit 297ff0b.*
