# Windows and Linux Porting Plan

This project is mostly Electron + Python, so Windows and Linux support is
practical. The macOS-specific work is concentrated around packaging, bundled
binaries, data paths, and system audio capture.

## Porting checklist

1. Make backend executable names and user-data paths platform-aware.
2. Resolve bundled and system `ffmpeg` per platform.
3. Resolve bundled and system Ollama per platform.
4. Add Electron Builder targets and assets for Windows and Linux.
5. Keep microphone-only recording working across platforms.
6. Add system-audio capture adapters:
   - macOS: current Core Audio Tap path.
   - Windows: WASAPI loopback.
   - Linux: PulseAudio/PipeWire monitor sources.
7. Adjust setup UI and settings copy for per-platform feature availability.
8. Add platform build/test jobs once each platform has a runnable package.

## Current first milestone

The first milestone is a microphone-only desktop app that can launch the
Python backend, store data in the correct per-user directory, transcribe audio,
and summarize using either a local, remote, or cloud AI provider.
