import os
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

out_dir = r"D:\nexus-inspect-release-v3\app\screenshots"
os.makedirs(out_dir, exist_ok=True)

opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--disable-gpu")
opts.add_argument("--window-size=1280,900")

driver = webdriver.Chrome(options=opts)
try:
    # 1. Landing Page
    driver.get("http://127.0.0.1:8000/#home")
    time.sleep(1.0)
    driver.save_screenshot(os.path.join(out_dir, "01_landing_page.png"))
    print("Saved 01_landing_page.png")

    # 2. Sign up to access dashboard
    driver.get("http://127.0.0.1:8000/#signup")
    time.sleep(0.5)
    email = f"shot_{int(time.time())}@example.com"
    driver.find_element(By.ID, "signupName").send_keys("Demo User")
    driver.find_element(By.ID, "signupEmail").send_keys(email)
    driver.find_element(By.ID, "signupPassword").send_keys("DemoPass12345!")
    driver.find_element(By.CSS_SELECTOR, "#signupForm button[type='submit']").click()
    time.sleep(1.5)

    # 3. Analyze page with repository checked
    driver.get("http://127.0.0.1:8000/#analyze")
    time.sleep(0.8)
    driver.find_element(By.ID, "analyzeUrl").send_keys("https://github.com/zoro-hue/smart-email-manager")
    driver.find_element(By.ID, "checkBtn").click()
    time.sleep(2.0)
    driver.save_screenshot(os.path.join(out_dir, "02_analyze_page.png"))
    print("Saved 02_analyze_page.png")

    # 4. Docs / Quickstart
    driver.get("http://127.0.0.1:8000/#docs/quickstart")
    time.sleep(1.0)
    driver.save_screenshot(os.path.join(out_dir, "03_documentation.png"))
    print("Saved 03_documentation.png")

    # 5. Mobile view
    driver.set_window_size(390, 844)
    driver.get("http://127.0.0.1:8000/#analyze")
    time.sleep(1.0)
    driver.save_screenshot(os.path.join(out_dir, "04_mobile_view.png"))
    print("Saved 04_mobile_view.png")

finally:
    driver.quit()
print("Screenshots captured successfully!")
