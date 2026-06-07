import pytest
import sys
import tkinter as tk
from loguru import logger
from unittest.mock import MagicMock


# Configure loguru for tests
logger.remove()  # Remove default handler
logger.add(sys.stdout, level="DEBUG")


@pytest.fixture(autouse=True)
def setup_test_environment():
    """Setup test environment for each test"""
    import os

    # Ensure clean state for each test
    yield

    # Cleanup test config file if it exists
    test_config_file = "push_to_talk_config_test.json"
    if os.path.exists(test_config_file):
        try:
            os.remove(test_config_file)
        except Exception:
            pass  # Ignore cleanup errors


# === GUI Testing Fixtures ===


@pytest.fixture(scope="session")
def mock_tk_root():
    """Create a single real Tk root shared across all GUI tests.

    Session-scoped so only one Tk interpreter is created per process —
    creating multiple tk.Tk() instances sequentially is unreliable and
    causes sporadic skip/failure on the second and later tests.

    Returns:
        tk.Tk: Real Tk root (withdrawn) shared for the session.
    """
    try:
        root = tk.Tk()
        root.withdraw()
        yield root
        try:
            root.destroy()
        except Exception:
            pass
    except Exception:
        pytest.skip("Cannot create Tk root window (headless environment)")


@pytest.fixture
def mock_gui_sections():
    """Create mocked GUI sections for testing

    Returns:
        dict: Dictionary of section name -> section class
    """
    from src.gui.api_section import APISection
    from src.gui.hotkey_section import HotkeySection
    from src.gui.settings_section import FeatureFlagsSection
    from src.gui.glossary_section import GlossarySection
    from src.gui.prompt_section import PromptSection

    yield {
        "api": APISection,
        "hotkey": HotkeySection,
        "feature_flags": FeatureFlagsSection,
        "glossary": GlossarySection,
        "prompt": PromptSection,
    }


@pytest.fixture
def prepared_config_gui(mock_tk_root, mock_gui_sections):
    """Create ConfigurationWindow with prepared GUI sections

    This fixture creates a ConfigurationWindow instance with:
    - Real Tk root (already set as default by mock_tk_root fixture)
    - Mocked section frames (no actual widgets)
    - Pre-initialized sections with test config values

    Usage:
        def test_something(prepared_config_gui):
            gui = prepared_config_gui
            # GUI is ready with all sections initialized
            ...

    Returns:
        ConfigurationWindow: Fully initialized GUI instance
    """
    from src.gui import ConfigurationWindow
    from src.push_to_talk import PushToTalkConfig

    config = PushToTalkConfig(openai_api_key="test-key")
    gui = ConfigurationWindow(config, config_file_path="push_to_talk_config_test.json")
    gui.root = mock_tk_root

    # Initialize sections with the real (withdrawn) Tk root as parent so CTk
    # widget constructors get a valid Tk ancestor without opening a visible window.
    root = mock_tk_root
    gui.api_section = mock_gui_sections["api"](root, root, root)
    gui.api_section.set_values(
        config.stt_provider,
        config.openai_api_key,
        config.deepgram_api_key,
        config.cerebras_api_key,
        config.gemini_api_key,
        config.custom_api_key,
        config.stt_model,
        config.refinement_provider,
        config.refinement_model,
        config.custom_endpoint,
        config.custom_stt_endpoint,
        config.custom_refinement_endpoint,
    )

    gui.hotkey_section = mock_gui_sections["hotkey"](root)
    gui.hotkey_section.set_values(
        config.hotkey,
        config.toggle_hotkey,
    )

    gui.feature_flags_section = mock_gui_sections["feature_flags"](root, root)
    gui.feature_flags_section.set_values(
        config.enable_text_refinement,
        config.enable_logging,
        config.enable_audio_feedback,
        config.debug_mode,
    )

    gui.glossary_section = mock_gui_sections["glossary"](root, root, config.custom_glossary)

    gui.prompt_section = mock_gui_sections["prompt"](root, root, config.custom_refinement_prompt)
    # Store the prompt value and mock get_prompt/set_prompt to use it
    gui.prompt_section._stored_prompt = config.custom_refinement_prompt
    gui.prompt_section.get_prompt = lambda: gui.prompt_section._stored_prompt
    gui.prompt_section.set_prompt = lambda p: setattr(
        gui.prompt_section, "_stored_prompt", p
    )

    return gui
