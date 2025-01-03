import aiohttp
import logging
from collections.abc import AsyncIterable
from webbrowser import get
from homeassistant.components import stt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([WISSTT(hass, config_entry)])


class WISSTT(stt.SpeechToTextEntity):
    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        self.url: str = config_entry.data["url"]
        self.backup_url: str = config_entry.data.get("backup_url", "")
        self.cert_validation: bool = config_entry.data["cert_validation"]
        self.model: str = config_entry.data["model"]
        self.detect_language: bool = config_entry.data["detect_language"]
        self.language: str = config_entry.data["language"]
        self.beam_size: int = config_entry.data["beam_size"]
        self.speaker: str = config_entry.data["speaker"]
        self.save_audio: bool = config_entry.data["save_audio"]

        self._attr_name = f"WIS STT {self.url} ({self.language})"
        self._attr_unique_id = f"{config_entry.entry_id[:7]}-stt"

    @property
    def supported_languages(self) -> list[str]:
        return [self.language]

    @property
    def supported_formats(self) -> list[stt.AudioFormats]:
        return [stt.AudioFormats.WAV]

    @property
    def supported_codecs(self) -> list[stt.AudioCodecs]:
        return [stt.AudioCodecs.PCM]

    @property
    def supported_bit_rates(self) -> list[stt.AudioBitRates]:
        return [stt.AudioBitRates.BITRATE_16]

    @property
    def supported_sample_rates(self) -> list[stt.AudioSampleRates]:
        return [stt.AudioSampleRates.SAMPLERATE_16000]

    @property
    def supported_channels(self) -> list[stt.AudioChannels]:
        return [stt.AudioChannels.CHANNEL_MONO]

    async def async_process_audio_stream(
    self, metadata: stt.SpeechMetadata, stream: AsyncIterable[bytes]
) -> stt.SpeechResult:
        _LOGGER.debug("process_audio_stream start")

        def session_creator(verify_ssl=None):
            if verify_ssl:
                return aiohttp.ClientSession()
            else:
                return aiohttp.ClientSession(connector=aiohttp.TCPConnector(verify_ssl=False))

        async def stream_reader(stream=None):
            async for chunk in stream:
                yield chunk

        async def attempt_stream(url: str):
            async with session_creator(self.cert_validation) as session:
                session.headers.update({'x-audio-codec': 'pcm'})
                session.headers.update({'x-audio-channel': '1'})
                session.headers.update({'x-audio-bits': '16'})
                session.headers.update({'x-audio-sample-rate': '16000'})
                params = {
                    'model': self.model,
                    'detect_language': str(self.detect_language),
                    'return_language': self.language,
                    'force_language': self.language,
                    'beam_size': self.beam_size,
                    'speaker': self.speaker,
                    'save_audio': str(self.save_audio)
                }
                async with session.post(url, params=params, data=stream_reader(stream=stream)) as resp:
                    _LOGGER.debug(f"HTTP Response Status: {resp.status}")
                    raw_response = await resp.text()
                    _LOGGER.debug(f"Raw Response: {raw_response}")
                    
                    if resp.status != 200:
                        raise Exception(f"HTTP error: {resp.status}")

                    try:
                        text = await resp.json(content_type=None)
                        return text
                    except Exception as e:
                        _LOGGER.error(f"Failed to parse JSON response: {e}")
                        raise

        try:
            _LOGGER.debug(f"Attempting STT with primary URL: {self.url}")
            text = await attempt_stream(self.url)
        except Exception as primary_error:
            _LOGGER.warning(f"Primary URL failed: {primary_error}")
            if hasattr(self, 'backup_url') and self.backup_url:
                try:
                    _LOGGER.debug(f"Attempting STT with backup URL: {self.backup_url}")
                    text = await attempt_stream(self.backup_url)
                except Exception as backup_error:
                    _LOGGER.error(f"Backup URL also failed: {backup_error}")
                    raise backup_error
            else:
                raise primary_error

        _LOGGER.info(f"process_audio_stream end: {text}")

        if "text" not in text:
            _LOGGER.error("Invalid response: Missing 'text' key in response")
            raise ValueError("Invalid response: Missing 'text' key in response")

        return stt.SpeechResult(text["text"], stt.SpeechResultState.SUCCESS)
