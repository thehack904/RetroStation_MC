# RetroStation MC Roadmap: Preview Channel Direction

This roadmap tracks the development path toward a mid-90s Preview Channel-style experience for RetroStation MC while using lessons from ErsatzTV Legacy for transcoding, playout, scheduling, GPU abstraction, and stream continuity.

The goal is not to compete with ErsatzTV. The goal is to let RSMC become the retro presentation/master-control layer while learning from proven playout and FFmpeg engineering patterns.

---

## Strategic Direction

RetroStation MC should evolve into a scene-driven broadcast presentation system:

```text
RetroStation MC
├── Preview Channel Renderer
├── Virtual Channel Presentation Layer
├── Scene/Overlay Engine
├── Playout Scheduler
├── Transcoding Layer
├── GPU Capability Layer
├── Stream Continuity Watchdog
└── HLS Output / External Source Integration
```

### Borrow concepts from ErsatzTV Legacy

- Transcoding profile structure
- FFmpeg command generation patterns
- Hardware acceleration abstraction
- Playout continuity concepts
- Scheduling concepts
- HLS health and stream continuity lessons

### Keep native to RSMC

- Preview Channel visual layout
- Virtual channel video-area selector
- Scene compositor
- Guide graphics
- Retro UI theme
- Admin/control UI
- Diagnostics specific to RSMC

---

## Version Plan

| Version | Focus | Intent |
|---|---|---|
| v1.2.0 | Architecture and HLS continuity | Establish boundaries and stabilize output health |
| v1.3.0 | GPU/transcoding abstraction | Prepare for hardware-accelerated encoding |
| v1.4.0 | Playout documents and scheduler | Introduce structured playout logic |
| v1.5.0 | Preview Channel scene model | Define the visual/presentation model |
| v1.6.0 | GPU renderer prototype | Evaluate accelerated layered rendering |
| v1.7.0 | Scheduler-to-renderer integration | Make playout drive the Preview Channel presentation |
| v1.8.0 | ErsatzTV-compatible source mode | Use ErsatzTV Legacy outputs as upstream sources |

---

# v1.2.0 — Architecture and Continuity Foundation

## Define RSMC playout/transcoding architecture boundary

**Goal:** Define the architectural boundary between RetroStation MC's presentation layer and the backend playout/transcoding layer.

**Tasks:**

- [ ] Document the future backend/frontend split.
- [ ] Identify ErsatzTV-inspired areas:
  - [ ] transcoding
  - [ ] playout
  - [ ] scheduling
  - [ ] GPU abstraction
  - [ ] stream continuity
- [ ] Identify native RSMC areas:
  - [ ] Preview Channel renderer
  - [ ] virtual channel presentation
  - [ ] overlays
  - [ ] guide graphics
  - [ ] admin/control UI
- [ ] Add this design to `docs/ARCHITECTURE.md`.

**Acceptance Criteria:**

- [ ] Architecture document clearly separates renderer, scheduler, playout, and transcoder responsibilities.
- [ ] No implementation is tightly coupled to ErsatzTV internals.
- [ ] Future work can be broken into independent issues.

---

## Create FFmpeg profile abstraction for RSMC

**Goal:** Create a first-pass FFmpeg profile abstraction that can eventually support software encoding and GPU-accelerated encoding.

**Tasks:**

- [ ] Add an internal model for FFmpeg profiles.
- [ ] Track resolution.
- [ ] Track video codec.
- [ ] Track audio codec.
- [ ] Track bitrate.
- [ ] Track preset.
- [ ] Track HLS segment length.
- [ ] Track encoder type.
- [ ] Track hardware acceleration provider.
- [ ] Add default software profile.
- [ ] Add placeholder providers for NVIDIA, Intel, AMD, and VAAPI.

**Acceptance Criteria:**

- [ ] Transcoding options are no longer hardcoded directly into command construction.
- [ ] Profiles can be selected by name.
- [ ] Existing behavior remains unchanged by default.

---

## Add stream continuity watchdog for HLS output

**Goal:** Add a watchdog that monitors HLS output health and detects stalled or starving playlists.

**Tasks:**

- [ ] Monitor latest segment modification time.
- [ ] Monitor playlist update time.
- [ ] Detect stale playlist windows.
- [ ] Log continuity warnings.
- [ ] Prepare restart hooks without enabling aggressive auto-restart yet.

**Acceptance Criteria:**

- [ ] Diagnostics can show whether HLS output is healthy.
- [ ] Stalled generation is detected within a reasonable time.
- [ ] No disruptive restart behavior is introduced yet.

---

# v1.3.0 — GPU and Transcoding Abstraction

## Implement GPU capability detection layer

**Goal:** Detect available GPU acceleration providers and expose them to the RSMC backend.

**Providers:**

- NVIDIA NVENC
- Intel QuickSync / QSV
- AMD AMF
- VAAPI
- software fallback

**Tasks:**

- [ ] Add detection commands.
- [ ] Detect FFmpeg encoder availability.
- [ ] Detect device visibility inside Docker.
- [ ] Report capabilities in Diagnostics.
- [ ] Avoid failing startup if GPU is unavailable.

**Acceptance Criteria:**

- [ ] Admin Diagnostics shows detected GPU/encoder support.
- [ ] Software fallback always works.
- [ ] Missing GPU support is reported clearly, not treated as fatal.

---

## Add hardware encoder profile mapping

**Goal:** Map RSMC FFmpeg profiles to hardware-specific encoder arguments.

**Tasks:**

- [ ] Add encoder mappings for `h264_nvenc`.
- [ ] Add encoder mappings for `h264_qsv`.
- [ ] Add encoder mappings for `h264_amf`.
- [ ] Add encoder mappings for `h264_vaapi`.
- [ ] Add encoder mappings for `libx264` fallback.
- [ ] Keep output format compatible with current HLS playback.
- [ ] Add logging that shows selected encoder path.

**Acceptance Criteria:**

- [ ] Profile selection produces valid FFmpeg arguments.
- [ ] Unsupported encoders fall back safely.
- [ ] Logs clearly identify the chosen encoder.

---

## Create transcoder command builder tests

**Goal:** Add test coverage around FFmpeg command generation before expanding GPU support further.

**Tasks:**

- [ ] Add tests for software profile command generation.
- [ ] Add tests for GPU profile command generation.
- [ ] Add fallback behavior tests.
- [ ] Add invalid profile tests.

**Acceptance Criteria:**

- [ ] Command builder behavior is covered by repeatable tests.
- [ ] Future FFmpeg changes can be validated without manual playback testing only.

---

# v1.4.0 — Playout Documents and Scheduler

## Introduce playout document schema

**Goal:** Create a simple playout document schema that can describe scheduled videos, virtual channels, promos, standby content, and Preview Channel blocks.

**Example:**

```json
{
  "channel": "preview-channel",
  "items": [
    {
      "type": "video",
      "source": "/media/promos/intro.mp4",
      "duration": 90
    },
    {
      "type": "virtual_channel",
      "source": "weather",
      "duration": 300
    }
  ]
}
```

**Tasks:**

- [ ] Define schema.
- [ ] Add validation.
- [ ] Add sample documents.
- [ ] Add documentation.

**Acceptance Criteria:**

- [ ] RSMC can parse a playout document.
- [ ] Invalid documents produce clear errors.
- [ ] Schema supports future renderer and scheduler work.

---

## Add basic playout scheduler service

**Goal:** Create a scheduler service that can step through a playout document and determine the current active item.

**Tasks:**

- [ ] Track active item.
- [ ] Track next item.
- [ ] Support looping schedules.
- [ ] Support fixed-duration items.
- [ ] Emit state for Diagnostics and renderer.

**Acceptance Criteria:**

- [ ] Scheduler can continuously loop a basic playout document.
- [ ] Current and next items are visible through an internal API.
- [ ] Scheduler does not directly render anything.

---

## Add standby video and fallback playout handling

**Goal:** Prevent dead-air behavior by adding fallback playout handling.

**Tasks:**

- [ ] Define fallback video behavior.
- [ ] Define fallback virtual channel behavior.
- [ ] Detect missing/unavailable scheduled item.
- [ ] Route to standby content instead of failing.

**Acceptance Criteria:**

- [ ] Missing media does not stop the channel.
- [ ] Fallback behavior is logged.
- [ ] Admin can identify why fallback was used.

---

# v1.5.0 — Preview Channel Scene Model

## Create Preview Channel scene model

**Goal:** Create the internal scene model for a mid-90s Preview Channel-style layout.

**Scene Regions:**

- video area
- guide/listings area
- virtual channel panel
- ticker
- clock/status region
- branding/logo area

**Tasks:**

- [ ] Define scene JSON.
- [ ] Define layer ordering.
- [ ] Define region sizing.
- [ ] Add default Preview Channel scene.
- [ ] Document scene structure.

**Acceptance Criteria:**

- [ ] Renderer can consume a scene definition.
- [ ] Scene model supports video and virtual channel regions.
- [ ] Layout is not hardcoded only in templates.

---

## Build virtual channel video-area selector

**Goal:** Allow the Preview Channel video area to show different virtual channel outputs or video playout content.

**Tasks:**

- [ ] Add source type selector for promo video.
- [ ] Add source type selector for weather virtual channel.
- [ ] Add source type selector for news virtual channel.
- [ ] Add source type selector for sports virtual channel.
- [ ] Add source type selector for updates/announcements.
- [ ] Add source type selector for standby video.
- [ ] Add admin configuration.
- [ ] Persist selection.
- [ ] Expose current source in Diagnostics.

**Acceptance Criteria:**

- [ ] Admin can choose what appears in the video area.
- [ ] Existing guide behavior is not broken.
- [ ] Unsupported/missing sources fall back cleanly.

---

## Add first-pass Preview Channel visual theme

**Goal:** Add a first-pass visual theme inspired by mid-90s cable preview channels without copying protected branding.

**Tasks:**

- [ ] Add color palette.
- [ ] Add blocky layout.
- [ ] Add ticker-style region.
- [ ] Add guide/listings panel.
- [ ] Add video preview window.
- [ ] Add safe generic branding.

**Acceptance Criteria:**

- [ ] Theme evokes 1990s cable-guide style.
- [ ] No trademarked names, logos, or exact branding are used.
- [ ] Theme can be enabled/disabled cleanly.

---

# v1.6.0 — GPU Renderer Prototype

## Prototype GPU-accelerated renderer path

**Goal:** Prototype a GPU-accelerated rendering path for the Preview Channel scene.

**Tasks:**

- [ ] Evaluate browser/WebGL or Electron-style rendering approach.
- [ ] Identify minimum viable accelerated compositor.
- [ ] Render layered scene with background.
- [ ] Render layered scene with guide.
- [ ] Render layered scene with video window.
- [ ] Render layered scene with ticker.
- [ ] Render layered scene with logo/clock.
- [ ] Document performance findings.

**Acceptance Criteria:**

- [ ] A prototype proves layered rendering can be GPU accelerated.
- [ ] Findings are documented.
- [ ] Decision is made whether to continue with browser/WebGL, Electron, or another renderer.

---

## Add renderer-to-HLS capture strategy document

**Goal:** Document how the rendered Preview Channel scene will become an HLS stream.

**Options To Evaluate:**

- browser capture
- headless Chromium capture
- Electron capture
- FFmpeg ingest
- native compositor output

**Acceptance Criteria:**

- [ ] Pros/cons are documented.
- [ ] Latency, quality, GPU use, and stability are compared.
- [ ] A recommended path is selected for implementation.

---

## Add motion/ticker timing engine

**Goal:** Create the timing model for smooth scrolling tickers and guide motion.

**Tasks:**

- [ ] Define ticker speed model.
- [ ] Define guide scroll timing.
- [ ] Add frame-rate target.
- [ ] Avoid timer drift.
- [ ] Add settings for slow/medium/fast movement.

**Acceptance Criteria:**

- [ ] Ticker and guide movement are controlled by a shared timing model.
- [ ] Motion settings are configurable.
- [ ] Timing does not depend on arbitrary sleep loops.

---

# v1.7.0 — Scheduler-to-Renderer Integration

## Integrate playout scheduler with Preview Channel renderer

**Goal:** Connect the scheduler state to the Preview Channel scene renderer.

**Tasks:**

- [ ] Renderer reads current scheduled item.
- [ ] Video area updates based on playout state.
- [ ] Virtual channel panels update based on scheduled source.
- [ ] Add transition-safe state changes.

**Acceptance Criteria:**

- [ ] Playout schedule drives the Preview Channel video area.
- [ ] Renderer does not directly own scheduling logic.
- [ ] Source changes do not require manual page refresh.

---

## Add transition model for video-area source changes

**Goal:** Add controlled transitions when the video area changes from one source to another.

**Tasks:**

- [ ] Add immediate cut.
- [ ] Add fade transition.
- [ ] Add optional wipe-style transition.
- [ ] Prevent black frames when possible.
- [ ] Log transition events.

**Acceptance Criteria:**

- [ ] Source changes are visually controlled.
- [ ] Transition failures fall back to a direct cut.
- [ ] Transitions do not stall HLS output.

---

## Add renderer health and frame timing diagnostics

**Goal:** Expose renderer health in Diagnostics.

**Metrics:**

- current scene
- current video-area source
- render FPS estimate
- dropped/stalled frame count if available
- last scene update
- last scheduler update

**Acceptance Criteria:**

- [ ] Admin Diagnostics can distinguish transcoder issues from renderer issues.
- [ ] Renderer state is visible without reading raw logs only.

---

# v1.8.0 — ErsatzTV-Compatible Source Integration

## Create ErsatzTV-compatible source integration mode

**Goal:** Allow ErsatzTV Legacy channel outputs to be used as sources inside RetroStation MC without competing against ErsatzTV.

**Tasks:**

- [ ] Add source type for external HLS/M3U8 channel.
- [ ] Add source health checks.
- [ ] Add admin configuration.
- [ ] Support ErsatzTV-generated streams as video-area sources.
- [ ] Document recommended integration pattern.

**Acceptance Criteria:**

- [ ] RSMC can use an ErsatzTV channel as a source.
- [ ] RSMC overlays/presents the source but does not replace ErsatzTV scheduling.
- [ ] Documentation clearly describes non-competitive integration.

---

## Add attribution and license review checklist

**Goal:** Add a formal checklist before copying or porting any implementation details from ErsatzTV Legacy or related FFmpeg image repositories.

**Tasks:**

- [ ] Identify license of referenced repo/code.
- [ ] Record whether code is copied, ported, or conceptually reimplemented.
- [ ] Add attribution rules.
- [ ] Add dependency/license notes to documentation.
- [ ] Ensure GPL/LGPL implications are understood before direct reuse.

**Acceptance Criteria:**

- [ ] Project has a license review checklist.
- [ ] Direct code reuse is blocked until reviewed.
- [ ] Conceptual learning and clean-room implementation are documented separately.

---

## Add external source continuity monitoring

**Goal:** Monitor external HLS/M3U8 sources such as ErsatzTV outputs.

**Tasks:**

- [ ] Validate playlist availability.
- [ ] Validate recent segment updates.
- [ ] Detect stale external sources.
- [ ] Switch to fallback when unavailable.
- [ ] Report source health in Diagnostics.

**Acceptance Criteria:**

- [ ] External source problems do not break the Preview Channel.
- [ ] Admin can see whether the issue is RSMC or upstream source health.

---

## Development Guardrails

- Do not directly copy ErsatzTV Legacy code until license compatibility is reviewed.
- Prefer clean-room implementation when possible.
- Keep RSMC focused on presentation, guide rendering, virtual channels, diagnostics, and admin UX.
- Treat ErsatzTV as a compatible upstream source, not a competitor.
- Keep software encoding as a safe fallback for every GPU path.
- Add tests before expanding FFmpeg command generation complexity.
- Avoid making FFmpeg the primary graphics engine for the Preview Channel UI.
- Keep scene rendering separate from playout scheduling.

---

## Definition of Done for This Roadmap Direction

RSMC reaches the intended direction when it can:

- [ ] Render a Preview Channel-style layout.
- [ ] Display a guide/listings area.
- [ ] Display a video area that can show media, virtual channels, or external sources.
- [ ] Use hardware acceleration where available.
- [ ] Fall back to software encoding cleanly.
- [ ] Monitor HLS/source continuity.
- [ ] Use structured playout documents.
- [ ] Drive presentation from a scheduler.
- [ ] Accept ErsatzTV Legacy output as an upstream source.
- [ ] Maintain clear architectural separation from ErsatzTV while learning from its proven design patterns.
