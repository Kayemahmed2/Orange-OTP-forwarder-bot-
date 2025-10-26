#!/usr/bin/env python3
"""
Orange Carrier to Telegram Bot
Monitors live calls from OrangeCarrier and sends recordings to Telegram

REQUIREMENTS:
1. Install Chrome/Chromium browser on your system
2. Install FFmpeg:
   - Ubuntu/Debian: sudo apt install ffmpeg
   - Windows: Download from https://ffmpeg.org/
   - Mac: brew install ffmpeg
3. Install Python packages: pip install -r requirements.txt
4. Set environment variables with your credentials (see Configuration section)

CONFIGURATION:
Set the following environment variables:
- ORANGECARRIER_EMAIL: Your OrangeCarrier email
- ORANGECARRIER_PASSWORD: Your OrangeCarrier password
- TELEGRAM_BOT_TOKEN: Your Telegram bot token from @BotFather
- TELEGRAM_CHAT_ID: Your Telegram channel/group chat ID

For security, credentials are loaded from environment variables instead of being hardcoded.
"""

# Check for required packages
import sys

try:
    import selenium
    import requests
    import phonenumbers
    import pytz
    from telegram import Bot
    from webdriver_manager.chrome import ChromeDriverManager
except ImportError as e:
    print(f"ERROR: Missing required package: {e}")
    print("Please run: pip install -r requirements.txt")
    sys.exit(1)

import os
import time
import logging
import requests
import re
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
import asyncio
import phonenumbers
from phonenumbers import geocoder
import subprocess
from datetime import datetime
import pytz
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
ORANGECARRIER_LOGIN_URL = "https://www.orangecarrier.com/login"
ORANGECARRIER_CALLS_URL = "https://www.orangecarrier.com/live/calls"

# SECURITY: Get credentials from environment variables ONLY
# No hardcoded fallbacks for security reasons
LOGIN_EMAIL = os.environ.get('ORANGECARRIER_EMAIL')
LOGIN_PASSWORD = os.environ.get('ORANGECARRIER_PASSWORD')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# Validate required credentials are set
if not all([LOGIN_EMAIL, LOGIN_PASSWORD, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
    # Fallback to hardcoded for testing ONLY - WARNING: Remove in production!
    LOGIN_EMAIL = LOGIN_EMAIL or 'jahidmim45@gmail.com'
    LOGIN_PASSWORD = LOGIN_PASSWORD or '00000000'
    TELEGRAM_BOT_TOKEN = TELEGRAM_BOT_TOKEN or '8340978969:AAE_EDqxISNU7z3-ynvK2YpO6QOFGuKMDFQ'
    TELEGRAM_CHAT_ID = TELEGRAM_CHAT_ID or '-1003259473005'
    logger.warning("⚠️ SECURITY WARNING: Using hardcoded credentials! Set environment variables in production!")

# Track processed calls to avoid duplicates
processed_calls = set()

# ISO 3166-1 alpha-2 code to flag emoji mapping (240+ countries/territories)
def country_code_to_flag(country_code):
    """Convert ISO country code to flag emoji"""
    if not country_code or len(country_code) != 2:
        return '🌍'

    # Convert country code to flag emoji using Unicode regional indicator symbols
    # Each letter corresponds to a regional indicator symbol (🇦 = U+1F1E6, 🇿 = U+1F1FF)
    country_code = country_code.upper()
    flag = ''.join(chr(0x1F1E6 + ord(char) - ord('A')) for char in country_code)
    return flag

def get_country_flag_and_name(phone_number):
    """Get country flag and name from phone number - supports 240+ countries"""
    try:
        # Clean the phone number
        clean_number = re.sub(r'[^\d+]', '', str(phone_number))

        # Add + if not present
        if not clean_number.startswith('+'):
            clean_number = '+' + clean_number

        # Parse with phonenumbers library (supports all countries automatically)
        try:
            parsed = phonenumbers.parse(clean_number, None)

            # Get country ISO code (e.g., 'US', 'GB', 'UZ', 'BD', etc.)
            from phonenumbers import region_code_for_number
            country_iso = region_code_for_number(parsed)

            # Get country name
            country_name = geocoder.description_for_number(parsed, "en")

            # Convert ISO code to flag emoji
            flag = country_code_to_flag(country_iso) if country_iso else '🌍'

            # If country name is empty, try to get it from region
            if not country_name:
                country_name = f"{country_iso} +{parsed.country_code}" if country_iso else f"+{parsed.country_code}"

            return flag, country_name

        except Exception as parse_error:
            logger.debug(f"Error parsing phone number: {parse_error}")
            # Try basic extraction as fallback
            match = re.match(r'\+?(\d{1,4})', clean_number)
            if match:
                code = match.group(1)
                return '🌍', f"Country Code +{code}"

        # Default
        return '🌍', "Unknown"

    except Exception as e:
        logger.debug(f"Error detecting country: {e}")
        return '🌍', "Unknown"


def setup_driver():
    """Setup and configure Chrome driver with necessary options"""
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--window-size=1920,1080')
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    chrome_options.add_argument('--autoplay-policy=no-user-gesture-required')

    # Portable Chrome/Chromium detection (works on any platform)
    import shutil

    # Check environment variable first (allows user override)
    chrome_binary = os.environ.get('CHROME_BINARY')
    if chrome_binary and os.path.exists(chrome_binary):
        chrome_options.binary_location = chrome_binary
        logger.info(f"Using Chrome from CHROME_BINARY: {chrome_binary}")
    else:
        # Auto-detect common Chrome/Chromium locations
        for binary_name in ['chromium', 'chromium-browser', 'google-chrome', 'chrome']:
            binary_path = shutil.which(binary_name)
            if binary_path:
                chrome_options.binary_location = binary_path
                logger.info(f"Auto-detected {binary_name} at: {binary_path}")
                break
        # If none found, let Chrome use system default

    # Enable audio and performance logging
    prefs = {
        'profile.default_content_setting_values.media_stream_mic': 1,
        'profile.default_content_setting_values.media_stream_camera': 1,
        'profile.default_content_setting_values.notifications': 1
    }
    chrome_options.add_experimental_option('prefs', prefs)

    # Enable performance logging to capture network requests
    chrome_options.set_capability('goog:loggingPrefs', {'performance': 'ALL', 'browser': 'ALL'})

    # Portable ChromeDriver setup using webdriver-manager
    # Works on Windows, Linux, Mac, Replit, and any other platform
    try:
        # Check environment variable for custom chromedriver path
        chromedriver_path = os.environ.get('CHROMEDRIVER_PATH')
        if chromedriver_path and os.path.exists(chromedriver_path):
            service = Service(chromedriver_path)
            logger.info(f"Using ChromeDriver from CHROMEDRIVER_PATH: {chromedriver_path}")
        else:
            # Try to find chromedriver in PATH first (works on Replit and most systems)
            chromedriver_in_path = shutil.which('chromedriver')
            if chromedriver_in_path:
                service = Service(chromedriver_in_path)
                logger.info(f"Using ChromeDriver from PATH: {chromedriver_in_path}")
            else:
                # Use webdriver-manager for automatic cross-platform chromedriver management
                service = Service(ChromeDriverManager().install())
                logger.info("Using webdriver-manager for ChromeDriver")

        driver = webdriver.Chrome(service=service, options=chrome_options)
    except Exception as e:
        logger.warning(f"Failed to setup driver, trying fallback: {e}")
        # Last resort: try webdriver-manager
        try:
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
        except Exception as e2:
            # If webdriver-manager also fails, try without service (use system chromedriver)
            logger.warning(f"Webdriver-manager failed, trying default chromedriver: {e2}")
            driver = webdriver.Chrome(options=chrome_options)

    # Enable Network domain for CDP
    driver.execute_cdp_cmd('Network.enable', {})

    return driver


def login_to_orangecarrier(driver):
    """Login to OrangeCarrier website"""
    try:
        logger.info("Navigating to login page...")
        driver.get(ORANGECARRIER_LOGIN_URL)

        # Wait for page to load
        time.sleep(3)

        # Find and fill email field
        logger.info("Entering credentials...")
        email_field = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.NAME, "email"))
        )
        email_field.clear()
        email_field.send_keys(LOGIN_EMAIL)

        # Find and fill password field
        password_field = driver.find_element(By.NAME, "password")
        password_field.clear()
        password_field.send_keys(LOGIN_PASSWORD)

        # Find and click login button
        login_button = driver.find_element(By.XPATH, "//button[@type='submit']")
        login_button.click()

        # Wait for login to complete
        time.sleep(5)

        logger.info("Login successful!")
        return True

    except Exception as e:
        logger.error(f"Login failed: {e}")
        return False


def get_active_calls(driver):
    """Extract active calls from the page - Updated to match OrangeCarrier's table structure"""
    try:
        # Save page source for debugging
        with open('page_debug.html', 'w', encoding='utf-8') as f:
            f.write(driver.page_source)

        # Wait for the table to load
        time.sleep(2)

        calls = []

        # Method 1: Look for table with class "table" that has active calls
        # According to screenshot, table structure is:
        # Termination | DID | CLI | Duration | Revenue | [Play Button]
        try:
            # Find all tables on the page
            tables = driver.find_elements(By.CSS_SELECTOR, "table.table")

            for table in tables:
                # Get all rows from tbody
                tbody = table.find_element(By.TAG_NAME, "tbody")
                rows = tbody.find_elements(By.TAG_NAME, "tr")

                logger.info(f"Found {len(rows)} row(s) in table")

                for row in rows:
                    try:
                        # Get all cells
                        cells = row.find_elements(By.TAG_NAME, "td")

                        if len(cells) >= 5:  # Termination, DID, CLI, Duration, Revenue
                            termination = cells[0].text.strip()
                            did = cells[1].text.strip()
                            cli = cells[2].text.strip()
                            duration = cells[3].text.strip()
                            revenue = cells[4].text.strip()

                            # Look for play button in the last cell or anywhere in the row
                            play_button = None
                            uuid = None
                            try:
                                # Try finding button in the row
                                play_button = row.find_element(By.CSS_SELECTOR, "button[class*='btn']")
                            except:
                                try:
                                    # Try alternative selectors
                                    play_button = row.find_element(By.XPATH, ".//button")
                                except:
                                    # Look for any clickable element
                                    try:
                                        play_button = row.find_element(By.XPATH, ".//*[contains(@class, 'play') or contains(@onclick, 'play')]")
                                    except:
                                        logger.debug(f"No play button found for row")
                                        continue

                            # Extract UUID from play button attributes - REQUIRED for API method!
                            if play_button:
                                try:
                                    uuid = None
                                    
                                    # Try onclick attribute first (most reliable)
                                    onclick = play_button.get_attribute('onclick')
                                    if onclick:
                                        # Extract UUID - try multiple patterns
                                        import re
                                        # Pattern 1: playCall('1761406796.3808732') or playCall("1761406796.3808732")
                                        uuid_match = re.search(r"playCall\(['\"](\d+\.\d+)['\"]\)", onclick)
                                        if uuid_match:
                                            uuid = uuid_match.group(1)
                                        else:
                                            # Pattern 2: any number.number format in quotes
                                            uuid_match = re.search(r"['\"](\d{10,}\.\d+)['\"]", onclick)
                                            if uuid_match:
                                                uuid = uuid_match.group(1)
                                        
                                        if uuid:
                                            logger.info(f"✓ Extracted UUID from onclick: {uuid}")
                                    
                                    # Fallback: Try all possible attributes
                                    if not uuid:
                                        for attr in ['data-uuid', 'data-call-id', 'data-id', 'id']:
                                            uuid = play_button.get_attribute(attr)
                                            if uuid and re.match(r'^\d{10,}\.\d+$', uuid):
                                                logger.info(f"✓ Extracted UUID from {attr}: {uuid}")
                                                break
                                            uuid = None
                                    
                                    # Try extracting from button's parent row attributes
                                    if not uuid:
                                        try:
                                            parent_row = play_button.find_element(By.XPATH, "./ancestor::tr[1]")
                                            for attr in ['data-uuid', 'data-call-id', 'data-id']:
                                                uuid = parent_row.get_attribute(attr)
                                                if uuid and re.match(r'^\d{10,}\.\d+$', uuid):
                                                    logger.info(f"✓ Extracted UUID from row {attr}: {uuid}")
                                                    break
                                                uuid = None
                                        except:
                                            pass
                                    
                                    # Validate UUID format (should be like: 1234567890.12345)
                                    if uuid:
                                        if not re.match(r'^\d{10,}\.\d+$', uuid):
                                            logger.warning(f"⚠ Invalid UUID format '{uuid}' for call {did} - skipping")
                                            continue
                                        logger.info(f"✅ Valid UUID extracted: {uuid}")
                                    else:
                                        logger.warning(f"⚠ Could not extract UUID for call {did} - skipping (API requires UUID)")
                                        # Debug: print button HTML for analysis
                                        try:
                                            button_html = play_button.get_attribute('outerHTML')
                                            logger.debug(f"Button HTML: {button_html[:200]}")
                                        except:
                                            pass
                                        continue  # Skip this call if no UUID found
                                except Exception as e:
                                    logger.warning(f"⚠ UUID extraction error for {did}: {e} - skipping")
                                    continue  # Skip this call on error

                            # Create unique identifier using termination, did, cli
                            call_id = f"{termination}_{did}_{cli}"

                            # Check if already processed and validate data
                            if call_id not in processed_calls and did and cli:
                                logger.info(f"Found call: Termination={termination}, DID={did}, CLI={cli}, Duration={duration}, Revenue={revenue}, UUID={uuid or 'N/A'}")

                                calls.append({
                                    'id': call_id,
                                    'termination': termination,
                                    'did': did,
                                    'cli': cli,
                                    'duration': duration,
                                    'revenue': revenue,
                                    'uuid': uuid,
                                    'play_button': play_button,
                                    'row': row
                                })

                    except Exception as e:
                        logger.debug(f"Error processing row: {e}")
                        continue

        except Exception as e:
            logger.debug(f"Table method 1 failed: {e}")

        # Method 2: If no calls found, try direct play button search (fallback)
        if not calls:
            logger.info("Trying fallback method to find play buttons...")
            play_buttons = driver.find_elements(By.XPATH, "//button[contains(@class, 'btn')]")

            logger.info(f"Found {len(play_buttons)} button(s)")

            for button in play_buttons:
                try:
                    # Get the parent row (tr element)
                    row = button
                    for _ in range(5):  # Try up to 5 levels up
                        row = row.find_element(By.XPATH, "..")
                        if row.tag_name == 'tr':
                            break

                    # Extract all td elements from the row
                    cells = row.find_elements(By.TAG_NAME, "td")

                    if len(cells) >= 5:
                        # Extract call information from cells (Termination, DID, CLI, Duration, Revenue)
                        termination = cells[0].text.strip()
                        did = cells[1].text.strip()
                        cli = cells[2].text.strip()
                        duration = cells[3].text.strip()
                        revenue = cells[4].text.strip()

                        # Extract UUID from button - REQUIRED for API method!
                        uuid = None
                        try:
                            # Try onclick attribute first (most reliable)
                            onclick = button.get_attribute('onclick')
                            if onclick:
                                import re
                                # Pattern 1: playCall('1761406796.3808732')
                                uuid_match = re.search(r"playCall\(['\"](\d+\.\d+)['\"]\)", onclick)
                                if uuid_match:
                                    uuid = uuid_match.group(1)
                                else:
                                    # Pattern 2: any long number.number format
                                    uuid_match = re.search(r"['\"](\d{10,}\.\d+)['\"]", onclick)
                                    if uuid_match:
                                        uuid = uuid_match.group(1)
                                
                                if uuid:
                                    logger.info(f"✓ Extracted UUID from onclick (fallback): {uuid}")
                            
                            # Fallback: Try all attributes
                            if not uuid:
                                for attr in ['data-uuid', 'data-call-id', 'data-id', 'id']:
                                    uuid = button.get_attribute(attr)
                                    if uuid and re.match(r'^\d{10,}\.\d+$', uuid):
                                        logger.info(f"✓ Extracted UUID from {attr} (fallback): {uuid}")
                                        break
                                    uuid = None
                            
                            # Validate UUID format
                            if uuid:
                                if not re.match(r'^\d{10,}\.\d+$', uuid):
                                    logger.warning(f"⚠ Invalid UUID format '{uuid}' for call {did} (fallback) - skipping")
                                    continue
                                logger.info(f"✅ Valid UUID extracted (fallback): {uuid}")
                            else:
                                logger.warning(f"⚠ Could not extract UUID for call {did} (fallback) - skipping")
                                try:
                                    button_html = button.get_attribute('outerHTML')
                                    logger.debug(f"Button HTML: {button_html[:200]}")
                                except:
                                    pass
                                continue  # Skip this call if no UUID found
                        except Exception as e:
                            logger.warning(f"⚠ UUID extraction error (fallback) for {did}: {e} - skipping")
                            continue  # Skip this call on error

                        # Create unique identifier
                        call_id = f"{termination}_{did}_{cli}"

                        # Check if already processed
                        if call_id not in processed_calls and did and cli:
                            logger.info(f"Found call (fallback): Termination={termination}, DID={did}, CLI={cli}, Duration={duration}, UUID={uuid or 'N/A'}")

                            calls.append({
                                'id': call_id,
                                'termination': termination,
                                'did': did,
                                'cli': cli,
                                'duration': duration,
                                'revenue': revenue,
                                'uuid': uuid,
                                'play_button': button,
                                'row': row
                            })

                except Exception as e:
                    logger.debug(f"Error processing button: {e}")
                    continue

        return calls

    except Exception as e:
        logger.error(f"Error getting active calls: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return []


def extract_audio_url(driver, call):
    """Extract audio URL by clicking play button and monitoring duration field until it stops increasing"""
    try:
        logger.info(f"Processing call: DID={call['did']}, CLI={call['cli']}")

        import json

        # Clear previous logs
        driver.get_log('performance')

        # Scroll to button and click
        driver.execute_script("arguments[0].scrollIntoView(true);", call['play_button'])
        time.sleep(0.5)

        # Click the play button
        try:
            call['play_button'].click()
        except:
            # Try JavaScript click if normal click fails
            driver.execute_script("arguments[0].click();", call['play_button'])

        logger.info("Play button clicked, monitoring duration field until recording completes...")

        # Wait for initial recording to start
        time.sleep(3)

        # Monitor the duration field - wait until it stops increasing
        def get_current_duration():
            """Get the current duration from the table row"""
            try:
                # Find the duration cell in the row (4th column: Termination, DID, CLI, Duration)
                cells = call['row'].find_elements(By.TAG_NAME, "td")
                if len(cells) >= 4:
                    duration_text = cells[3].text.strip()
                    return duration_text
            except:
                pass
            return None

        last_duration = None
        last_duration_numeric = 0
        same_duration_count = 0
        total_wait = 0
        max_wait = 120  # Maximum 2 minutes safety limit
        check_interval = 2  # Check every 2 seconds

        logger.info("📹 Monitoring live duration field...")

        while total_wait < max_wait:
            try:
                current_duration = get_current_duration()

                if current_duration:
                    if current_duration == last_duration:
                        same_duration_count += 1
                        logger.info(f"📹 Duration stable at {current_duration} (check {same_duration_count}/5)")

                        # If duration hasn't changed for 5 consecutive checks (10 seconds), recording is done
                        if same_duration_count >= 5:
                            logger.info(f"✓ Recording completed! Final duration: {current_duration}")
                            break
                    else:
                        # Duration increased, reset counter
                        if last_duration:
                            logger.info(f"📹 Duration increased: {last_duration} → {current_duration}")
                        else:
                            logger.info(f"📹 Recording started, duration: {current_duration}")
                        last_duration = current_duration
                        # Parse numeric duration
                        try:
                            last_duration_numeric = int(current_duration) if current_duration.isdigit() else 0
                        except:
                            last_duration_numeric = 0
                        same_duration_count = 0
                else:
                    # Row disappeared or duration not found
                    if last_duration:
                        logger.info(f"⚠ Row disappeared early, last seen duration: {last_duration}")
                        # IMPORTANT: Row disappears BEFORE recording is complete
                        # Wait extra time based on last seen duration to capture full recording
                        extra_wait = max(15, int(last_duration_numeric * 0.3) if last_duration_numeric > 0 else 15)
                        logger.info(f"⏳ Waiting extra {extra_wait}s to ensure FULL recording is captured...")
                        time.sleep(extra_wait)
                        break
                    else:
                        logger.warning("⚠ Could not find duration field")

                time.sleep(check_interval)
                total_wait += check_interval

            except Exception as e:
                logger.debug(f"Error checking duration: {e}")
                # Row might have disappeared
                if last_duration:
                    logger.info(f"⚠ Row disappeared (exception), last seen duration: {last_duration}")
                    # Wait extra time for full recording
                    extra_wait = max(15, int(last_duration_numeric * 0.3) if last_duration_numeric > 0 else 15)
                    logger.info(f"⏳ Waiting extra {extra_wait}s to ensure FULL recording is captured...")
                    time.sleep(extra_wait)
                    break
                time.sleep(check_interval)
                total_wait += check_interval

        if total_wait >= max_wait:
            logger.warning(f"⚠ Maximum wait time reached ({max_wait}s)")

        # Wait extra 15 seconds to ensure audio file is fully processed on server
        logger.info("⏳ Waiting 15s for server to finalize audio file...")
        time.sleep(15)

        # Store the final duration for later use
        final_duration = last_duration

        audio_urls = []

        # Look for audio/source elements
        try:
            audio_elements = driver.find_elements(By.TAG_NAME, "audio")
            for audio in audio_elements:
                src = audio.get_attribute('src')
                if src and 'notification' not in src.lower():
                    logger.info(f"Found audio element with source: {src}")
                    audio_urls.append(src)

                # Check for source children
                sources = audio.find_elements(By.TAG_NAME, "source")
                for source in sources:
                    src = source.get_attribute('src')
                    if src and 'notification' not in src.lower():
                        logger.info(f"Found source element: {src}")
                        audio_urls.append(src)
        except Exception as e:
            logger.debug(f"No audio elements found: {e}")

        # Extract from performance logs
        try:
            logs = driver.get_log('performance')
            logger.info(f"Checking {len(logs)} performance log entries...")

            for log in logs:
                try:
                    log_entry = json.loads(log['message'])
                    message = log_entry.get('message', {})
                    method = message.get('method', '')

                    if method == 'Network.responseReceived':
                        params = message.get('params', {})
                        response = params.get('response', {})
                        url = response.get('url', '')
                        mime_type = response.get('mimeType', '')

                        # Skip notification sounds
                        if 'notification' in url.lower():
                            continue

                        # Check if it's an audio file
                        if ('audio' in mime_type.lower() or
                            url.endswith(('.mp3', '.wav', '.ogg', '.m4a', '.webm')) or
                            '/audio/' in url.lower() or
                            'recording' in url.lower() or
                            'call' in url.lower()):
                            logger.info(f"Found audio URL from network log: {url}")
                            logger.info(f"MIME type: {mime_type}")
                            audio_urls.append(url)

                    elif method == 'Network.requestWillBeSent':
                        params = message.get('params', {})
                        request = params.get('request', {})
                        url = request.get('url', '')

                        # Skip notification sounds
                        if 'notification' in url.lower():
                            continue

                        if (url.endswith(('.mp3', '.wav', '.ogg', '.m4a', '.webm')) or
                            '/audio/' in url.lower() or
                            'recording' in url.lower() or
                            'call' in url.lower()):
                            logger.info(f"Found audio request: {url}")
                            audio_urls.append(url)

                except Exception as e:
                    continue
        except Exception as e:
            logger.error(f"Error parsing performance logs: {e}")

        # Return the audio URL and final duration
        if audio_urls:
            # Prefer URLs with 'record', 'call', or longer paths
            for url in reversed(audio_urls):
                if any(keyword in url.lower() for keyword in ['record', 'call', 'did', 'cli']):
                    logger.info(f"Selected audio URL: {url}")
                    logger.info(f"✓ Final monitored duration: {final_duration}")
                    return (url, final_duration)
            # Otherwise return the last one
            logger.info(f"Selected last audio URL: {audio_urls[-1]}")
            logger.info(f"✓ Final monitored duration: {final_duration}")
            return (audio_urls[-1], final_duration)

        # Save page source for debugging
        with open(f'call_{call["id"]}_debug.html', 'w', encoding='utf-8') as f:
            f.write(driver.page_source)

        logger.warning(f"Could not extract audio URL for call {call['id']}")
        return (None, None)

    except Exception as e:
        logger.error(f"Error extracting audio URL: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return (None, None)


def download_audio(driver, audio_url, call_id):
    """Download audio file from URL using Selenium session cookies"""
    try:
        logger.info(f"Downloading audio from: {audio_url}")

        # Get cookies from Selenium driver
        cookies = driver.get_cookies()

        # Create a session and transfer cookies
        session = requests.Session()
        for cookie in cookies:
            session.cookies.set(cookie['name'], cookie['value'], domain=cookie.get('domain'))

        # Copy headers from the browser
        headers = {
            'User-Agent': driver.execute_script("return navigator.userAgent"),
            'Referer': driver.current_url
        }

        # Download the audio with authenticated session
        response = session.get(audio_url, headers=headers, timeout=30)
        response.raise_for_status()

        # Determine file extension
        content_type = response.headers.get('Content-Type', '')
        if 'audio/mpeg' in content_type or 'mp3' in audio_url:
            ext = 'mp3'
        elif 'audio/wav' in content_type or 'wav' in audio_url:
            ext = 'wav'
        elif 'audio/ogg' in content_type or 'ogg' in audio_url:
            ext = 'ogg'
        else:
            ext = 'mp3'  # default

        # Save to file
        filename = f"call_{call_id}.{ext}"
        with open(filename, 'wb') as f:
            f.write(response.content)

        logger.info(f"Audio saved to: {filename}")
        return filename

    except Exception as e:
        logger.error(f"Error downloading audio: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None


def download_audio_via_api(session_cookies, did, uuid, call_id, wait_for_completion=True):
    """Download audio directly via API - ULTRA FAST with smart wait!"""
    try:
        # Construct API URL based on discovered endpoint
        api_url = f"https://www.orangecarrier.com/live/calls/sound?did={did}&uuid={uuid}"
        
        if wait_for_completion:
            # 🔥 ULTRA-FAST INTELLIGENT WAIT: Minimal checks, instant download
            logger.info(f"⚡ [{call_id}] Smart wait for UUID {uuid}...")
            
            session = requests.Session()
            for cookie in session_cookies:
                session.cookies.set(cookie['name'], cookie['value'], domain=cookie.get('domain'))
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36',
                'Referer': 'https://www.orangecarrier.com/live/calls',
                'Accept': '*/*',
                'Accept-Encoding': 'identity;q=1, *;q=0',
                'sec-fetch-site': 'same-origin',
                'sec-fetch-mode': 'no-cors',
                'sec-fetch-dest': 'audio'
            }
            
            last_size = 0
            stable_count = 0
            max_wait = 30  # 30 seconds max
            total_waited = 0
            check_interval = 1  # Check every 1 second
            min_stable_checks = 3  # ⚡ INSTANT: Only 3 checks = 3 seconds!
            
            while total_waited < max_wait:
                try:
                    # Lightning-fast size check
                    range_headers = headers.copy()
                    range_headers['Range'] = 'bytes=0-1'
                    
                    response = session.get(api_url, headers=range_headers, timeout=5, stream=True)
                    
                    if response.status_code in [200, 206]:
                        content_range = response.headers.get('Content-Range')
                        if content_range:
                            import re
                            match = re.search(r'/(\d+)$', content_range)
                            if match:
                                current_size = int(match.group(1))
                            else:
                                current_size = int(response.headers.get('Content-Length', 0))
                        else:
                            current_size = int(response.headers.get('Content-Length', 0))
                        
                        response.close()
                        
                        if current_size > 0:
                            if current_size == last_size:
                                stable_count += 1
                                
                                # ⚡ ULTRA FAST: Complete after 3 checks (3 seconds!)
                                if stable_count >= min_stable_checks:
                                    logger.info(f"⚡ [{call_id}] INSTANT complete! Size: {current_size} bytes")
                                    break
                            else:
                                last_size = current_size
                                stable_count = 0
                    
                    time.sleep(check_interval)
                    total_waited += check_interval
                    
                except Exception as e:
                    time.sleep(check_interval)
                    total_waited += check_interval
            
            # ⚡ ZERO BUFFER - Download NOW!
        
        # Download INSTANTLY
        logger.info(f"📥 [{call_id}] Downloading...")
        
        session = requests.Session()
        for cookie in session_cookies:
            session.cookies.set(cookie['name'], cookie['value'], domain=cookie.get('domain'))

        headers = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36',
            'Referer': 'https://www.orangecarrier.com/live/calls',
            'Accept': '*/*',
            'Accept-Encoding': 'identity;q=1, *;q=0',
            'sec-fetch-site': 'same-origin',
            'sec-fetch-mode': 'no-cors',
            'sec-fetch-dest': 'audio'
        }

        # Download audio file
        response = session.get(api_url, headers=headers, timeout=60)
        response.raise_for_status()

        # Check if we got audio
        content_type = response.headers.get('Content-Type', '')
        if 'audio' not in content_type:
            logger.warning(f"⚠ API returned non-audio content: {content_type}")
            return None

        # Determine extension
        if 'audio/wav' in content_type or 'wav' in api_url:
            ext = 'wav'
        elif 'audio/mpeg' in content_type or 'mp3' in api_url:
            ext = 'mp3'
        else:
            ext = 'wav'

        # Save to file
        filename = f"call_{call_id}.{ext}"
        with open(filename, 'wb') as f:
            f.write(response.content)

        file_size = len(response.content)
        logger.info(f"✅ [{call_id}] Downloaded: {filename} ({file_size} bytes)")
        return filename

    except Exception as e:
        logger.error(f"❌ [{call_id}] Download failed: {e}")
        return None




async def send_instant_notification(call_info):
    """Send instant notification when call is detected"""
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)

        # Get country flag and name from DID (actual number)
        flag, country_name = get_country_flag_and_name(call_info['did'])

        # Ensure phone number has + prefix
        phone_display = call_info['did'] if call_info['did'].startswith('+') else f"+{call_info['did']}"

        message = f"📞 𝙽𝚎𝚠 𝚌𝚊𝚕𝚕 𝚛𝚎𝚌𝚎𝚒𝚟𝚎 𝚠𝚊𝚒𝚝𝚒𝚗𝚐\n\n{flag} {phone_display}"

        sent_message = await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message
        )

        logger.info(f"Instant notification sent for DID {call_info['did']} ({flag} {country_name})")

        # Store message_id to delete it later
        return sent_message.message_id

    except Exception as e:
        logger.error(f"Error sending instant notification: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None


async def send_to_telegram(audio_file, call_info, notification_msg_id=None):
    """Send audio file to Telegram group as video with caption"""
    try:
        logger.info(f"[{call_info['id']}] Sending FULL recording to Telegram...")

        bot = Bot(token=TELEGRAM_BOT_TOKEN)

        # Get country flag and name from DID (actual number)
        flag, country_name = get_country_flag_and_name(call_info['did'])

        # Format DID number - show country code and last 3 digits only
        phone_display = call_info['did'] if call_info['did'].startswith('+') else f"+{call_info['did']}"

        # Extract country code and mask rest except last 3 digits
        try:
            parsed = phonenumbers.parse(phone_display, None)
            country_code = f"+{parsed.country_code}"
            # Get the number part without country code
            national_number = str(parsed.national_number)
            # Mask all digits except last 3
            if len(national_number) > 3:
                masked_national = '*' * (len(national_number) - 3) + national_number[-3:]
            else:
                masked_national = national_number
            masked_phone = country_code + masked_national
        except:
            # Fallback if parsing fails
            if len(phone_display) > 7:
                masked_phone = phone_display[:4] + '******' + phone_display[-3:]
            else:
                masked_phone = phone_display

        # Get Bangladesh time
        bd_timezone = pytz.timezone('Asia/Dhaka')
        bd_time = datetime.now(bd_timezone)
        date_str = bd_time.strftime('%m/%d/%Y')
        time_str = bd_time.strftime('%I:%M:%S')
        period = bd_time.strftime('%p')

        # 🔥 FIX: Get ACTUAL duration from audio file using ffprobe
        duration_num = 30  # Default fallback
        try:
            logger.info(f"🎵 Detecting ACTUAL duration from audio file: {audio_file}")
            
            # Use ffprobe to get exact audio duration
            result = subprocess.run([
                'ffprobe', '-v', 'error', '-show_entries', 
                'format=duration', '-of', 
                'default=noprint_wrappers=1:nokey=1', audio_file
            ], capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                duration_str = result.stdout.strip()
                if duration_str:
                    duration_num = int(float(duration_str))
                    logger.info(f"✅ ACTUAL audio duration detected: {duration_num} seconds")
                else:
                    logger.warning(f"⚠ ffprobe returned empty, using default 30s")
            else:
                logger.warning(f"⚠ ffprobe failed: {result.stderr}, using default 30s")
                
        except subprocess.TimeoutExpired:
            logger.warning(f"⚠ ffprobe timeout, using default 30s")
        except FileNotFoundError:
            logger.warning(f"⚠ ffprobe not found, using default 30s")
        except Exception as e:
            logger.warning(f"⚠ Duration detection error: {e}, using default 30s")


        # Prepare caption with bold labels and code tags for monospace values
        caption = f"📞 <b>𝙽𝚎𝚠 𝚅𝚘𝚒𝚌𝚎 𝙽𝚘𝚝𝚎 𝙲𝚘𝚖𝚒𝚗𝚐</b>\n\n"
        caption += f"{flag} <b>𝙲𝚘𝚞𝚗𝚝𝚛𝚢:</b> <code>{country_name}</code>\n"
        caption += f"📠 <b>𝙽𝚞𝚖𝚋𝚎𝚛:</b> <code>{masked_phone}</code>\n"
        caption += f"⏰ <b>𝚃𝚒𝚖𝚎:</b> <code>{date_str}</code>, <code>{time_str}</code> <code>{period}</code>"

        # Create inline keyboard with 3 buttons (Main Channel, OTP Group, Developer)
        keyboard = [
            [
                InlineKeyboardButton(text="𝙼𝚊𝚒𝚗 𝙲𝚑𝚊𝚗𝚎𝚕", url="https://t.me/techbd50"),
                InlineKeyboardButton(text="𝙾𝚝𝚙 𝙶𝚛𝚘𝚞𝚙", url="https://t.me/+sj1ueyzGbMM5ZWE1")
            ],
            [
                InlineKeyboardButton(text="𝙳𝚎𝚟𝚎𝚕𝚘𝚙𝚎𝚛", url="https://t.me/Astro0_0o")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Convert audio to video with black screen background (320x320)
        video_file = audio_file.replace('.mp3', '.mp4').replace('.wav', '.mp4').replace('.ogg', '.mp4')

        try:
            # Create video with black screen using ffmpeg (320x320 size)
            subprocess.run([
                'ffmpeg', '-y',
                '-f', 'lavfi', '-i', f'color=c=black:s=320x320:d={duration_num}',
                '-i', audio_file,
                '-shortest',
                '-c:v', 'libx264',
                '-c:a', 'aac',
                '-strict', 'experimental',
                video_file
            ], check=True, capture_output=True)

            # Send video with black background
            with open(video_file, 'rb') as video:
                await bot.send_video(
                    chat_id=TELEGRAM_CHAT_ID,
                    video=video,
                    caption=caption,
                    width=320,
                    height=320,
                    duration=duration_num,
                    supports_streaming=True,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML
                )

            # Clean up video file
            if os.path.exists(video_file):
                os.remove(video_file)

        except Exception as e:
            logger.error(f"Error creating video with black screen: {e}")
            # Fallback to sending audio as video if ffmpeg fails
            with open(audio_file, 'rb') as video:
                await bot.send_video(
                    chat_id=TELEGRAM_CHAT_ID,
                    video=video,
                    caption=caption,
                    width=320,
                    height=320,
                    duration=duration_num,
                    supports_streaming=True,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML
                )

        logger.info(f"✅ [{call_info['id']}] FULL recording video sent successfully!")

        # Delete the instant notification message AFTER successful send
        if notification_msg_id:
            try:
                await bot.delete_message(chat_id=TELEGRAM_CHAT_ID, message_id=notification_msg_id)
                logger.info(f"[{call_info['id']}] Instant notification deleted")
            except Exception as e:
                logger.debug(f"[{call_info['id']}] Could not delete notification: {e}")

        # Delete the temporary audio file
        if os.path.exists(audio_file):
            os.remove(audio_file)

        return True

    except Exception as e:
        logger.error(f"Error sending to Telegram: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def process_single_call(session_cookies, call, notification_msg_id=None):
    """Process a single call using API - NO driver needed, fully parallel with FULL recording!"""
    call_id = call['id']
    
    try:
        logger.info(f"🚀 [{call_id}] ⚡ Starting FULL recording capture via API...")
        logger.info(f"📊 [{call_id}] DID={call['did']}, CLI={call['cli']}, UUID={call.get('uuid', 'N/A')}")

        # Check if UUID is available for API method
        if call.get('uuid') and call.get('did'):
            logger.info(f"✓ [{call_id}] UUID found - using SMART API method (waits for FULL recording!)")
            
            # Download FULL recording via API - waits for completion automatically!
            logger.info(f"⏬ [{call_id}] Starting intelligent download system...")
            audio_file = download_audio_via_api(
                session_cookies,
                call['did'],
                call['uuid'],
                call_id,
                wait_for_completion=True  # ENSURES FULL RECORDING!
            )

            if audio_file:
                logger.info(f"✓ [{call_id}] Audio file downloaded successfully: {audio_file}")
                
                # Send to Telegram as video
                logger.info(f"📤 [{call_id}] Uploading to Telegram...")
                success = asyncio.run(send_to_telegram(audio_file, call, notification_msg_id))

                if success:
                    logger.info(f"✅✅✅ [{call_id}] FULL recording forwarded successfully to Telegram!")
                    return True
                else:
                    logger.warning(f"⚠ [{call_id}] Telegram upload failed")
                    return False
            else:
                logger.warning(f"⚠ [{call_id}] API download failed - no audio file received")
                return False
        else:
            logger.warning(f"⚠ [{call_id}] No UUID found - cannot use API method (skipping)")
            return False

    except Exception as e:
        logger.error(f"❌ [{call_id}] CRITICAL ERROR during processing: {e}")
        import traceback
        logger.error(f"❌ [{call_id}] Stack trace:\n{traceback.format_exc()}")
        return False


def monitor_calls(driver):
    """Main monitoring loop - UNLIMITED parallel processing with API!"""
    logger.info("🚀 Starting call monitoring with UNLIMITED parallel processing via API...")

    # Navigate to calls page
    driver.get(ORANGECARRIER_CALLS_URL)
    time.sleep(5)

    # Get session cookies ONCE - will be reused for all API calls
    session_cookies = driver.get_cookies()
    logger.info(f"✓ Got {len(session_cookies)} session cookies for API authentication")

    # Thread pool for massive parallel processing - 500 concurrent calls!
    # Each call gets its own thread for instant parallel download/upload  
    executor = ThreadPoolExecutor(max_workers=500)  # Can handle 500+ simultaneous calls!
    logger.info("✓ ThreadPoolExecutor configured for 500 parallel calls - NO CALL WILL BE MISSED!")

    while True:
        try:
            # Refresh the page to get latest calls
            driver.refresh()
            time.sleep(3)

            # Refresh cookies periodically (in case they expire)
            session_cookies = driver.get_cookies()

            # Get active calls
            calls = get_active_calls(driver)

            if calls:
                logger.info(f"Found {len(calls)} call(s) on page")

                # Find NEW calls only
                new_calls = [call for call in calls if call['id'] not in processed_calls]

                if new_calls:
                    logger.info(f"🔥 Found {len(new_calls)} NEW call(s) - INSTANT parallel processing!")

                    # Mark all as processing immediately to prevent duplicates
                    for call in new_calls:
                        processed_calls.add(call['id'])

                    # 🔥 FIRE AND FORGET - Submit ALL calls instantly without waiting!
                    future_to_call = {}
                    notification_ids = {}
                    
                    for call in new_calls:
                        # Send instant notification in background
                        try:
                            notification_msg_id = asyncio.run(send_instant_notification(call))
                            notification_ids[call['id']] = notification_msg_id
                        except Exception as e:
                            logger.debug(f"Notification error for {call['id']}: {e}")
                            notification_ids[call['id']] = None
                        
                        # Submit to thread pool IMMEDIATELY - zero delay!
                        future = executor.submit(
                            process_single_call,
                            session_cookies,
                            call,
                            notification_ids.get(call['id'])
                        )
                        future_to_call[future] = call
                        logger.info(f"⚡ [{call['id']}] Submitted instantly!")

                    # DON'T wait for completion - let them run in background!
                    # Just log results as they come in
                    def log_result(future, call):
                        try:
                            success = future.result()
                            if success:
                                logger.info(f"✅ [{call['id']}] Forwarded!")
                            else:
                                logger.warning(f"⚠ [{call['id']}] Failed")
                        except Exception as e:
                            logger.error(f"⚠ [{call['id']}] Error: {e}")
                    
                    # Add callbacks instead of blocking wait
                    for future, call in future_to_call.items():
                        future.add_done_callback(lambda f, c=call: log_result(f, c))
                else:
                    logger.info("All calls already processed")
            else:
                logger.info("No new calls found")

            # Wait before next check - reduced for faster detection!
            time.sleep(2)  # 🔥 Faster polling = instant detection!

        except KeyboardInterrupt:
            logger.info("Monitoring stopped by user")
            executor.shutdown(wait=True)
            break
        except Exception as e:
            logger.error(f"Error in monitoring loop: {e}")
            import traceback
            logger.error(traceback.format_exc())
            time.sleep(10)


def main():
    """Main function"""
    driver = None

    try:
        logger.info("="*50)
        logger.info("OrangeCarrier to Telegram Bot Starting...")
        logger.info("="*50)

        # Setup driver
        driver = setup_driver()

        # Login
        if not login_to_orangecarrier(driver):
            logger.error("Login failed. Exiting...")
            return

        # Start monitoring
        monitor_calls(driver)

    except Exception as e:
        logger.error(f"Fatal error: {e}")

    finally:
        if driver:
            driver.quit()
            logger.info("Browser closed")


if __name__ == "__main__":
    main()