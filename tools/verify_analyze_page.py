"""Comprehensive Browser verification script for NEXUS INSPECT Analyze Page."""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from tests.test_ingest import make_zip

MOCK_REPO = [
    ("repo/app.py", b"import os\nprint('hello')\n"),
    ("repo/README.md", b"# Test Repo\n"),
]

def run_verification():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,900")
    
    driver = webdriver.Chrome(options=opts)
    try:
        print("[1/7] Navigating to http://127.0.0.1:8000/#signup")
        driver.get("http://127.0.0.1:8000/#signup")
        time.sleep(1.0)
        
        email = f"analyzer_{int(time.time())}@example.com"
        driver.find_element(By.ID, "signupName").send_keys("Analyze Verifier")
        driver.find_element(By.ID, "signupEmail").send_keys(email)
        driver.find_element(By.ID, "signupPassword").send_keys("TestPass12345!")
        driver.find_element(By.CSS_SELECTOR, '#signupForm button[type="submit"]').click()
        time.sleep(1.5)
        
        print("[2/7] Navigating to #analyze")
        driver.get("http://127.0.0.1:8000/#analyze")
        time.sleep(1.0)
        
        url_input = driver.find_element(By.ID, "analyzeUrl")
        check_btn = driver.find_element(By.ID, "checkBtn")
        branch_sel = driver.find_element(By.ID, "branch")
        start_btn = driver.find_element(By.ID, "startBtn")
        err_div = driver.find_element(By.ID, "analyzeErr")
        note_div = driver.find_element(By.ID, "checkNote")
        
        print("Initial branch disabled:", not branch_sel.is_enabled())
        print("Initial start disabled:", not start_btn.is_enabled())
        
        # Test 1: Invalid URL
        print("[3/7] Testing invalid URL error state")
        url_input.clear()
        url_input.send_keys("https://not-github.com/bad/repo")
        check_btn.click()
        time.sleep(0.5)
        print("Error displayed on invalid URL:", err_div.text)
        assert len(err_div.text) > 0, "Expected error text for invalid URL"
        assert not branch_sel.is_enabled(), "Branch select should remain disabled"
        
        # Test 2: Public GitHub Repo (zoro-hue/smart-email-manager)
        print("[4/7] Testing public repo check (zoro-hue/smart-email-manager)")
        url_input.clear()
        url_input.send_keys("https://github.com/zoro-hue/smart-email-manager")
        check_btn.click()
        
        # Wait up to 10s for inspection to resolve
        for _ in range(20):
            time.sleep(0.5)
            if branch_sel.is_enabled():
                break
        
        print("Branch enabled:", branch_sel.is_enabled())
        print("Check note:", note_div.text)
        options = branch_sel.find_elements(By.TAG_NAME, "option")
        for opt in options:
            print(" -", opt.get_attribute("value"), ":", opt.text)
        
        assert branch_sel.is_enabled(), "Branch select should be enabled after check"
        assert start_btn.is_enabled(), "Start button should be enabled after check"
        assert len(options) >= 1, "Branch select should contain default branch"
        
        # Test 3: Repeated Check clicks (cache verification)
        print("[5/7] Testing repeated click / cache")
        t0 = time.time()
        check_btn.click()
        time.sleep(0.2)
        print("Repeated click took:", round(time.time() - t0, 3), "s; branch still enabled:", branch_sel.is_enabled())
        assert branch_sel.is_enabled(), "Branch select should remain enabled"
        
        # Test 4: ZIP upload functionality
        print("[6/7] Testing ZIP upload functionality")
        zip_path = os.path.abspath("test_upload_sample.zip")
        with open(zip_path, "wb") as f:
            f.write(make_zip(MOCK_REPO))
        
        try:
            zip_input = driver.find_element(By.ID, "zipFile")
            zip_input.send_keys(zip_path)
            time.sleep(0.5)
            assert start_btn.is_enabled(), "Start button should be enabled after selecting ZIP"
            
            # Click Start Analysis for ZIP
            start_btn.click()
            time.sleep(2.0)
            print("Successfully submitted ZIP analysis!")
        finally:
            if os.path.exists(zip_path):
                os.remove(zip_path)
        
        # Test 5: Mobile layout check (390px width)
        print("[7/7] Testing mobile layout (390x844)")
        driver.set_window_size(390, 844)
        time.sleep(0.5)
        assert url_input.is_displayed(), "URL input visible on mobile"
        assert check_btn.is_displayed(), "Check button visible on mobile"
        assert branch_sel.is_displayed(), "Branch dropdown visible on mobile"
        print("Mobile verification passed!")
        
        print("\nALL BROWSER VERIFICATION CHECKS PASSED SUCCESSFULLY!")
    finally:
        driver.quit()

if __name__ == "__main__":
    run_verification()
