import time
import csv
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

def scrape_easr():
    """
    Scrapes the Maharashtra eASR (Annual Statement of Rates) website.
    Uses Selenium in headless mode to navigate the ASP.NET WebForms and extract the tables.
    """
    options = webdriver.ChromeOptions()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')

    print("Setting up Chrome driver...")
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    try:
        print("Loading eASR page...")
        driver.get("https://easr.igrmaharashtra.gov.in/eASRCommon.aspx?hDistName=Bombaymains")
        
        # Wait for district dropdown to be present
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "ctl00_ContentPlaceHolder5_ddlDistrict"))
        )

        print("Selecting Village 'कुलाबा डिव्हीजन' (Colaba Division)...")
        village_dropdown = Select(driver.find_element(By.ID, "ctl00_ContentPlaceHolder5_ddlVillage"))
        village_dropdown.select_by_value("1") # 1 is 'कुलाबा डिव्हीजन'
        
        # Wait for the ASP.NET postback to complete
        time.sleep(3) 
        
        print("Selecting 'Survey No'...")
        try:
            survey_radio = driver.find_element(By.ID, "ctl00_ContentPlaceHolder5_rdbBymains_0")
            survey_radio.click()
            # Wait for postback
            time.sleep(3) 
        except Exception as e:
            print("Could not click radio button, it might be already selected.")

        print("Waiting for data table...")
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "ctl00_ContentPlaceHolder5_grdUrbanSubZoneWiseRate"))
        )

        csv_filename = "easr_colaba_data.csv"
        print(f"Extracting data to {csv_filename}...")
        
        with open(csv_filename, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            
            # Loop through pages if pagination exists
            page = 1
            while True:
                print(f"Scraping Page {page}...")
                table = driver.find_element(By.ID, "ctl00_ContentPlaceHolder5_grdUrbanSubZoneWiseRate")
                rows = table.find_elements(By.TAG_NAME, "tr")
                
                for i, row in enumerate(rows):
                    cols = row.find_elements(By.XPATH, "./th | ./td")
                    row_data = [col.text.strip() for col in cols]
                    
                    # Avoid writing header multiple times and skip pagination row at the bottom
                    if page > 1 and i == 0:
                        continue 
                    if "1 2 3" in row_data[0] or (len(row_data) > 0 and len(row_data[0]) <= 3 and row_data[0].isdigit()):
                         continue # skip pagination row itself
                         
                    if row_data:
                         writer.writerow(row_data)

                # Check for next page
                try:
                    next_page_num = str(page + 1)
                    next_page_link = driver.find_element(By.XPATH, f"//a[contains(@href, 'Page${next_page_num}') and text()='{next_page_num}']")
                    next_page_link.click()
                    page += 1
                    time.sleep(3) # Wait for table to reload
                except Exception:
                    print("No more pages found.")
                    break
                
        print("Scraping completed successfully!")
        
    except Exception as e:
        print("Error during scraping:", e)
        # Capture page source for debugging
        with open("error_page.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
    finally:
        driver.quit()

if __name__ == "__main__":
    scrape_easr()
