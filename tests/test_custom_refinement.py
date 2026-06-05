import pytest
from src.push_to_talk import PushToTalkConfig, PushToTalkApp


class TestCustomRefinement:
    def test_config_custom_provider_fields(self):
        """Test that PushToTalkConfig supports custom provider fields."""
        config = PushToTalkConfig(
            stt_provider="custom",
            refinement_provider="custom",
            custom_api_key="test-custom-key",
            custom_stt_endpoint="http://localhost:8000/v1",
            custom_refinement_endpoint="http://localhost:11434/v1",
        )
        assert config.stt_provider == "custom"
        assert config.refinement_provider == "custom"
        assert config.custom_api_key == "test-custom-key"
        assert config.get_custom_stt_endpoint() == "http://localhost:8000/v1"
        assert config.get_custom_refinement_endpoint() == "http://localhost:11434/v1"

    def test_legacy_custom_endpoint_fallback(self):
        """Test legacy custom_endpoint still works as a fallback."""
        config = PushToTalkConfig(custom_endpoint="http://localhost:11434/v1")

        assert config.get_custom_stt_endpoint() == "http://localhost:11434/v1"
        assert config.get_custom_refinement_endpoint() == "http://localhost:11434/v1"

    def test_app_initialization_with_custom_provider(self, mocker):
        """Test that PushToTalkApp correctly initializes refiner with custom settings."""
        # Mock TextRefinerFactory to verify calls
        mock_factory = mocker.patch("src.push_to_talk.TextRefinerFactory")

        # Mock TextInserter (pynput.keyboard fails in headless environment)
        mocker.patch("src.push_to_talk.TextInserter")
        # Also mock HotkeyService as it might also use pynput
        mocker.patch("src.push_to_talk.HotkeyService")
        # Mock AudioRecorder to avoid audio device issues
        mocker.patch("src.push_to_talk.AudioRecorder")

        config = PushToTalkConfig(
            stt_provider="openai",  # Set this to avoid Deepgram key validation
            refinement_provider="custom",
            custom_api_key="test-custom-key",
            custom_refinement_endpoint="http://localhost:11434/v1",
            openai_api_key="should-ignore-this",
            enable_text_refinement=True,
        )

        # We need to mock environment variables to avoid validation errors if keys aren't present
        mocker.patch.dict("os.environ", {"OPENAI_API_KEY": "env-key"})

        _ = PushToTalkApp(config=config)

        # Verify create_refiner was called with correct arguments
        mock_factory.create_refiner.assert_called_once()
        call_args = mock_factory.create_refiner.call_args
        assert call_args[1]["provider"] == "custom"
        assert call_args[1]["api_key"] == "test-custom-key"
        assert call_args[1]["base_url"] == "http://localhost:11434/v1"

    def test_app_initialization_with_custom_stt_provider(self, mocker):
        """Test that PushToTalkApp initializes custom OpenAI-compatible STT."""
        mock_transcriber_factory = mocker.patch("src.push_to_talk.TranscriberFactory")

        mocker.patch("src.push_to_talk.TextInserter")
        mocker.patch("src.push_to_talk.HotkeyService")
        mocker.patch("src.push_to_talk.AudioRecorder")

        config = PushToTalkConfig(
            stt_provider="custom",
            stt_model="whisper-large-v3",
            custom_stt_endpoint="http://localhost:8000/v1",
            enable_text_refinement=False,
        )

        _ = PushToTalkApp(config=config)

        mock_transcriber_factory.create_transcriber.assert_called_once()
        call_args = mock_transcriber_factory.create_transcriber.call_args
        assert call_args[1]["provider"] == "custom"
        assert call_args[1]["api_key"] == "local"
        assert call_args[1]["model"] == "whisper-large-v3"
        assert call_args[1]["base_url"] == "http://localhost:8000/v1"

    def test_custom_stt_requires_endpoint(self):
        """Test that custom STT fails clearly without an endpoint."""
        from src.exceptions import ConfigurationError

        config = PushToTalkConfig(
            stt_provider="custom",
            enable_text_refinement=False,
        )

        with pytest.raises(ConfigurationError, match="custom STT endpoint URL"):
            PushToTalkApp(config=config)

    def test_text_refiner_factory_custom_creation(self, mocker):
        """Test that TextRefinerFactory creates TextRefinerOpenAI for custom provider."""
        from src.text_refiner_factory import TextRefinerFactory

        # Mock TextRefinerOpenAI
        mock_openai_refiner = mocker.patch("src.text_refiner_factory.TextRefinerOpenAI")

        _ = TextRefinerFactory.create_refiner(
            provider="custom",
            api_key="test-key",
            model="llama3",
            base_url="http://custom.url/v1",
        )

        mock_openai_refiner.assert_called_once_with(
            api_key="test-key", model="llama3", base_url="http://custom.url/v1"
        )

    def test_config_validation_custom_provider(self):
        """Test validation for custom provider."""
        # Valid
        config = PushToTalkConfig(
            stt_provider="custom",
            refinement_provider="custom",
        )
        assert config.stt_provider == "custom"
        assert config.refinement_provider == "custom"

        # Invalid
        with pytest.raises(ValueError):
            PushToTalkConfig(refinement_provider="invalid_provider")
