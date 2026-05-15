try:
    import sounddevice as sd
    import numpy as np
    AUDIO_AVAILABLE = True
except ImportError:
    sd = None
    np = None
    AUDIO_AVAILABLE = False
import wave
import threading
import time
import atexit
from pathlib import Path
from typing import Optional
import logging
import json

logger = logging.getLogger(__name__)

# Global cleanup to prevent resource leaks
def cleanup_sounddevice():
    """Clean up sounddevice resources on exit"""
    try:
        if sd is not None:
            sd._terminate()
    except (AttributeError, RuntimeError, Exception) as e:
        # Log but don't raise - this is cleanup code
        logger.debug(f"Error during sounddevice cleanup: {e}")

atexit.register(cleanup_sounddevice)


class AudioRecorder:
    def __init__(self, sample_rate: int = 44100, channels: int = 1):
        if not AUDIO_AVAILABLE:
            raise ImportError("Audio dependencies not available. Please install sounddevice and numpy.")

        self.sample_rate = sample_rate
        self.channels = channels
        self.recording = False
        self.paused = False
        self.audio_data = []
        self.recording_thread: Optional[threading.Thread] = None

        # Thread safety lock for audio_data access
        self.audio_lock = threading.Lock()
        # Separate lock for pause state
        self.pause_lock = threading.Lock()

        # Simple state - no persistence for now
        self.stream = None
    
    def _load_state(self):
        """No persistence - start fresh each time."""
        self.recording = False
        self.audio_data = []
    
    def _save_state(self):
        """No persistence - do nothing."""
        pass
    
    def _clear_state(self):
        """No persistence - do nothing.""" 
        pass
        
    def start_recording(self) -> None:
        """Start recording audio from the microphone."""
        if self.recording:
            logger.warning("Recording is already in progress")
            return

        self.recording = True

        # Clear audio data with thread safety
        with self.audio_lock:
            self.audio_data = []

        logger.info("Creating recording thread...")
        self.recording_thread = threading.Thread(target=self._record)
        self.recording_thread.start()
        logger.info("Started recording thread")

        # Give thread a moment to start
        time.sleep(0.2)
        if not self.recording:
            logger.error("Recording failed to start - thread ended immediately")
        else:
            logger.info("Recording appears to be active")
        
    def stop_recording(self) -> None:
        """Stop recording audio."""
        if not self.recording:
            logger.warning("No recording in progress")
            return

        self.recording = False
        with self.pause_lock:
            self.paused = False
        if self.recording_thread:
            self.recording_thread.join(timeout=5.0)  # Add timeout to prevent hanging
            self.recording_thread = None  # Clean up reference

        logger.info("Stopped recording")

    def pause_recording(self) -> None:
        """Pause the current recording."""
        if not self.recording:
            logger.warning("No recording in progress to pause")
            return
        with self.pause_lock:
            if self.paused:
                logger.warning("Recording is already paused")
                return
            self.paused = True
        logger.info("Recording paused")

    def resume_recording(self) -> None:
        """Resume a paused recording."""
        if not self.recording:
            logger.warning("No recording in progress to resume")
            return
        with self.pause_lock:
            if not self.paused:
                logger.warning("Recording is not paused")
                return
            self.paused = False
        logger.info("Recording resumed")

    def is_paused(self) -> bool:
        """Check if recording is currently paused."""
        with self.pause_lock:
            return self.paused
        
    def _record(self) -> None:
        """Internal method to handle the recording process."""
        stream = None
        try:
            logger.info(f"Starting audio stream with sample_rate={self.sample_rate}, channels={self.channels}")
            stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                callback=self._audio_callback,
                blocksize=1024
            )
            self.stream = stream  # Store reference for cleanup
            stream.start()
            logger.info("Audio stream started successfully")
            
            while self.recording:
                time.sleep(0.1)
            logger.info("Recording loop ended")
            
        except Exception as e:
            logger.error(f"Error during recording: {e}")
            logger.error(f"Available audio devices: {sd.query_devices()}")
            self.recording = False
        finally:
            # Ensure stream is always properly closed
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                    logger.info("Audio stream closed")
                except (AttributeError, RuntimeError, Exception) as e:
                    logger.warning(f"Error closing audio stream: {e}")
            self.stream = None
            
    def _audio_callback(self, indata, frames, time, status):
        """Callback function for audio input stream."""
        if status:
            logger.warning(f"Audio callback status: {status}")
        if self.recording and not self.is_paused():
            # Thread-safe append to audio_data (skip when paused)
            with self.audio_lock:
                self.audio_data.append(indata.copy())
            
    def save_recording(self, filepath: Path) -> bool:
        """Save the recorded audio to a WAV file."""
        # Thread-safe check and copy of audio data
        with self.audio_lock:
            if not self.audio_data:
                logger.error("No audio data to save")
                return False
            # Create a copy to release lock quickly
            audio_data_copy = self.audio_data.copy()

        try:
            # Convert list of numpy arrays to single array
            audio_array = np.concatenate(audio_data_copy, axis=0)

            # Ensure the directory exists
            filepath.parent.mkdir(parents=True, exist_ok=True)

            # Save as WAV file
            with wave.open(str(filepath), 'wb') as wav_file:
                wav_file.setnchannels(self.channels)
                wav_file.setsampwidth(2)  # 16-bit
                wav_file.setframerate(self.sample_rate)

                # Convert float32 to int16
                audio_int16 = (audio_array * 32767).astype(np.int16)
                wav_file.writeframes(audio_int16.tobytes())

            logger.info(f"Audio saved to {filepath}")

            # Clear audio data after successful save (thread-safe)
            with self.audio_lock:
                self.audio_data = []
            self.recording = False

            return True

        except Exception as e:
            logger.error(f"Error saving audio: {e}")
            return False
            
    def get_recording_duration(self) -> float:
        """Get the duration of the current recording in seconds."""
        # Thread-safe read of audio data
        with self.audio_lock:
            if not self.audio_data:
                return 0.0
            total_frames = sum(len(chunk) for chunk in self.audio_data)
        return total_frames / self.sample_rate
        
    def is_recording(self) -> bool:
        """Check if currently recording."""
        return self.recording
    
    def __del__(self):
        """Cleanup resources when instance is destroyed."""
        try:
            if self.recording:
                self.stop_recording()
            if self.stream:
                try:
                    self.stream.stop()
                    self.stream.close()
                except (AttributeError, RuntimeError, Exception) as e:
                    logger.debug(f"Error closing stream in __del__: {e}")
            if self.recording_thread and self.recording_thread.is_alive():
                self.recording_thread.join(timeout=1.0)
        except (AttributeError, RuntimeError, Exception) as e:
            logger.debug(f"Error in __del__: {e}")


def _downmix_to_mono(chunk):
    if chunk.ndim == 1:
        return chunk.reshape(-1, 1)
    if chunk.shape[1] == 1:
        return chunk
    return np.mean(chunk, axis=1, keepdims=True)


def _find_linux_monitor_device():
    devices = sd.query_devices()
    for idx, device in enumerate(devices):
        name = str(device.get('name', '')).lower()
        if device.get('max_input_channels', 0) > 0 and 'monitor' in name:
            return idx
    return None


def _find_windows_loopback_device():
    try:
        default_output = sd.default.device[1]
        if default_output is not None and default_output >= 0:
            return int(default_output)
    except Exception:
        pass

    devices = sd.query_devices()
    for idx, device in enumerate(devices):
        hostapi = sd.query_hostapis(device.get('hostapi', 0))
        if 'wasapi' in str(hostapi.get('name', '')).lower() and device.get('max_output_channels', 0) > 0:
            return idx
    return None


def find_system_audio_device():
    """Return a likely loopback/monitor device index for the current platform."""
    import sys

    if sys.platform == 'win32':
        return _find_windows_loopback_device()
    if sys.platform.startswith('linux'):
        return _find_linux_monitor_device()
    return None


class SystemAudioRecorder(AudioRecorder):
    """Record microphone and system loopback/monitor audio into a stereo WAV."""

    def __init__(self, sample_rate: int = 48000):
        super().__init__(sample_rate=sample_rate, channels=1)
        self.mic_audio_data = []
        self.system_audio_data = []
        self.mic_stream = None
        self.system_stream = None
        self.system_device = find_system_audio_device()
        if self.system_device is None:
            raise RuntimeError("No system audio loopback/monitor device found")

    def start_recording(self) -> None:
        if self.recording:
            logger.warning("Recording is already in progress")
            return

        self.recording = True
        with self.audio_lock:
            self.mic_audio_data = []
            self.system_audio_data = []
            self.audio_data = []

        logger.info("Creating system-audio recording thread...")
        self.recording_thread = threading.Thread(target=self._record)
        self.recording_thread.start()
        time.sleep(0.2)
        if not self.recording:
            logger.error("System-audio recording failed to start")

    def _system_extra_settings(self):
        import sys

        if sys.platform == 'win32' and hasattr(sd, 'WasapiSettings'):
            return sd.WasapiSettings(loopback=True)
        return None

    def _record(self) -> None:
        mic_stream = None
        system_stream = None
        try:
            device_info = sd.query_devices(self.system_device)
            system_channels = max(1, min(2, int(device_info.get('max_output_channels') or device_info.get('max_input_channels') or 1)))
            logger.info(f"Starting dual audio streams with system_device={self.system_device}, system_channels={system_channels}")

            mic_stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                callback=self._mic_callback,
                blocksize=1024,
            )
            system_kwargs = {
                "samplerate": self.sample_rate,
                "channels": system_channels,
                "device": self.system_device,
                "callback": self._system_callback,
                "blocksize": 1024,
            }
            extra_settings = self._system_extra_settings()
            if extra_settings is not None:
                system_kwargs["extra_settings"] = extra_settings

            system_stream = sd.InputStream(**system_kwargs)
            self.mic_stream = mic_stream
            self.system_stream = system_stream
            self.stream = mic_stream

            mic_stream.start()
            system_stream.start()
            logger.info("Dual audio streams started successfully")

            while self.recording:
                time.sleep(0.1)
        except Exception as e:
            logger.error(f"Error during system-audio recording: {e}")
            logger.error(f"Available audio devices: {sd.query_devices()}")
            self.recording = False
        finally:
            for stream in (mic_stream, system_stream):
                if stream is not None:
                    try:
                        stream.stop()
                        stream.close()
                    except (AttributeError, RuntimeError, Exception) as e:
                        logger.warning(f"Error closing stream: {e}")
            self.mic_stream = None
            self.system_stream = None
            self.stream = None

    def _mic_callback(self, indata, frames, time_info, status):
        if status:
            logger.warning(f"Mic callback status: {status}")
        if self.recording and not self.is_paused():
            with self.audio_lock:
                self.mic_audio_data.append(_downmix_to_mono(indata.copy()))

    def _system_callback(self, indata, frames, time_info, status):
        if status:
            logger.warning(f"System callback status: {status}")
        if self.recording and not self.is_paused():
            with self.audio_lock:
                self.system_audio_data.append(_downmix_to_mono(indata.copy()))

    def save_recording(self, filepath: Path) -> bool:
        with self.audio_lock:
            if not self.mic_audio_data and not self.system_audio_data:
                logger.error("No audio data to save")
                return False
            mic_chunks = self.mic_audio_data.copy()
            system_chunks = self.system_audio_data.copy()

        try:
            mic = np.concatenate(mic_chunks, axis=0) if mic_chunks else np.zeros((0, 1), dtype=np.float32)
            system = np.concatenate(system_chunks, axis=0) if system_chunks else np.zeros((0, 1), dtype=np.float32)
            frames = max(len(mic), len(system))
            if len(mic) < frames:
                mic = np.pad(mic, ((0, frames - len(mic)), (0, 0)))
            if len(system) < frames:
                system = np.pad(system, ((0, frames - len(system)), (0, 0)))

            stereo = np.concatenate([mic[:frames], system[:frames]], axis=1)
            stereo = np.clip(stereo, -1.0, 1.0)

            filepath.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(filepath), 'wb') as wav_file:
                wav_file.setnchannels(2)
                wav_file.setsampwidth(2)
                wav_file.setframerate(self.sample_rate)
                wav_file.writeframes((stereo * 32767).astype(np.int16).tobytes())

            logger.info(f"Stereo system-audio recording saved to {filepath}")
            with self.audio_lock:
                self.mic_audio_data = []
                self.system_audio_data = []
                self.audio_data = []
            self.recording = False
            return True
        except Exception as e:
            logger.error(f"Error saving system-audio recording: {e}")
            return False
