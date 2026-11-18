"""Automated end-to-end browser journey verification for NEXUS INSPECT."""
import time
import unittest
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.action_chains import ActionChains
    SELENIUM_AVAILABLE = True
except ImportError:  # optional browser driver; not in requirements.txt
    SELENIUM_AVAILABLE = False


@unittest.skipUnless(SELENIUM_AVAILABLE, 'selenium is not installed; this optional browser test is skipped')
class BrowserJourneyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1280,900")
        try:
            cls.driver = webdriver.Chrome(options=opts)
        except Exception as exc:
            raise unittest.SkipTest(f"Chrome WebDriver not available: {exc}")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "driver") and cls.driver:
            cls.driver.quit()

    def setUp(self):
        if hasattr(self, "driver") and self.driver:
            try:
                self.driver.get("http://127.0.0.1:8000/")
                self.driver.delete_all_cookies()
                self.driver.execute_script("localStorage.clear(); sessionStorage.clear();")
            except Exception:
                pass

    def test_guest_landing_page_and_mega_menu(self):
        d = self.driver
        d.get("http://127.0.0.1:8000/#home")
        time.sleep(1.0)
        guest = d.find_element(By.CSS_SELECTOR, "#gnavActions .auth-guest-only")
        user = d.find_element(By.CSS_SELECTOR, "#gnavActions .auth-user-only")
        self.assertFalse(guest.get_attribute("hidden"))
        self.assertTrue(user.get_attribute("hidden"))

        # Mega menu
        prod_btn = d.find_element(By.CSS_SELECTOR, 'button[data-menu="product"]')
        ActionChains(d).move_to_element(prod_btn).perform()
        time.sleep(0.5)
        mega = d.find_element(By.ID, "mega")
        self.assertIn("open", mega.get_attribute("class"))
        self.assertGreater(mega.size["height"], 250)

    def test_auth_redirect_and_session_sync(self):
        d = self.driver
        d.get("http://127.0.0.1:8000/#home")
        time.sleep(0.5)
        # Click Analyze when logged out
        d.find_element(By.CSS_SELECTOR, '#heroCta a[href="#analyze"]').click()
        time.sleep(0.5)
        self.assertIn("signin", d.execute_script("return location.hash"))

        # Sign up
        d.get("http://127.0.0.1:8000/#signup")
        time.sleep(0.5)
        test_email = f"journey_{int(time.time())}@example.com"
        d.find_element(By.ID, "signupName").send_keys("Journey User")
        d.find_element(By.ID, "signupEmail").send_keys(test_email)
        d.find_element(By.ID, "signupPassword").send_keys("Passw0rd123!")
        d.find_element(By.CSS_SELECTOR, '#signupForm button[type="submit"]').click()
        time.sleep(1.5)

        # Return to landing page
        d.get("http://127.0.0.1:8000/#home")
        time.sleep(0.8)
        guest = d.find_element(By.CSS_SELECTOR, "#gnavActions .auth-guest-only")
        user = d.find_element(By.CSS_SELECTOR, "#gnavActions .auth-user-only")
        self.assertTrue(guest.get_attribute("hidden"))
        self.assertFalse(user.get_attribute("hidden"))

    def test_docs_pager_buttons(self):
        d = self.driver
        d.get("http://127.0.0.1:8000/#docs/quickstart")
        time.sleep(0.8)
        prev_btn = d.find_element(By.CSS_SELECTOR, ".doc-pager .btn-doc-pager:first-child")
        next_btn = d.find_element(By.CSS_SELECTOR, ".doc-pager .btn-doc-pager:last-child")
        self.assertIn("Introduction", prev_btn.text)
        self.assertIn("Installation", next_btn.text)

    def test_nexus_ai_assistant_interaction(self):
        d = self.driver
        d.get("http://127.0.0.1:8000/#home")
        time.sleep(1.0)

        # 1. Floating capsule is visible
        capsule = d.find_element(By.ID, "nexusAiCapsule")
        self.assertTrue(capsule.is_displayed())
        self.assertIn("Ask Nexus", capsule.text)

        # 2. Panel starts collapsed
        panel = d.find_element(By.ID, "nexusAiPanel")
        self.assertIn("collapsed", panel.get_attribute("class"))

        # 3. Click capsule opens panel
        capsule.click()
        time.sleep(0.5)
        self.assertNotIn("collapsed", panel.get_attribute("class"))

        # 4. Suggestions are visible
        chips = d.find_elements(By.CSS_SELECTOR, "#nexusAiSuggestions .ai-chip")
        self.assertGreater(len(chips), 0)

        # 5. Click a suggestion chip
        chips[0].click()
        time.sleep(1.5)

        # 6. Verify user message and assistant reply exist
        msgs = d.find_elements(By.CSS_SELECTOR, "#nexusAiMessages .ai-msg")
        self.assertGreaterEqual(len(msgs), 3)  # greeting + user + assistant

        # Assistant reply has substantive content
        last_msg = msgs[-1]
        self.assertIn("ai-msg-assistant", last_msg.get_attribute("class"))
        self.assertTrue(len(last_msg.text) > 20)

        # 7. Close button collapses panel
        close_btn = d.find_element(By.ID, "nexusAiClose")
        close_btn.click()
        time.sleep(0.4)
        self.assertIn("collapsed", panel.get_attribute("class"))


if __name__ == "__main__":
    unittest.main()
