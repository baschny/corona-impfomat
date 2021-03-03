#!/usr/bin/env python3

import argparse

from selenium.webdriver.chrome.options import Options
from selenium import webdriver
import time
import sys
import datetime
import os
import glob
import boto3
# noinspection PyPackageRequirements
import dateutil.tz
import yaml

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

from selenium.webdriver.chrome.webdriver import WebDriver
import json

screenshot_index = 1

# SES and mail configuration
SENDER = os.environ.get('SENDER')
RECIPIENT = os.environ.get('RECIPIENT')
AWS_REGION = os.environ.get('SES_AWS_REGION')
CHARSET = "UTF-8"
OUT_PATH = "../out"


class Error(Exception):
    """Base class for exceptions in this module."""
    pass


def create_multipart_message(
        sender: str, recipients: list, title: str, text: str = None, html: str = None, attachments: list = None) \
        -> MIMEMultipart:
    """
    Creates a MIME multipart message object.
    Uses only the Python `email` standard library.
    Emails, both sender and recipients, can be just the email string or have the format 'The Name <the_email@host.com>'.

    :param sender: The sender.
    :param recipients: List of recipients. Needs to be a list, even if only one recipient.
    :param title: The title of the email.
    :param text: The text version of the email body (optional).
    :param html: The html version of the email body (optional).
    :param attachments: List of files to attach in the email.
    :return: A `MIMEMultipart` to be used to send the email.
    """
    multipart_content_subtype = 'alternative' if text and html else 'mixed'
    msg = MIMEMultipart(multipart_content_subtype)
    msg['Subject'] = title
    msg['From'] = sender
    msg['To'] = ', '.join(recipients)

    # Record the MIME types of both parts - text/plain and text/html.
    # According to RFC 2046, the last part of a multipart message, in this case the HTML message, is best and preferred.
    if text:
        part = MIMEText(text, 'plain')
        msg.attach(part)
    if html:
        part = MIMEText(html, 'html')
        msg.attach(part)

    # Add attachments
    for attachment in attachments or []:
        with open(attachment, 'rb') as f:
            part = MIMEApplication(f.read())
            part.add_header('Content-Disposition', 'attachment', filename=os.path.basename(attachment))
            msg.attach(part)

    return msg


def send_mail(
        title: str, text: str = None, html: str = None, attachments: list = None) -> dict:
    """
    Send email to recipients. Sends one mail to all recipients.
    The sender needs to be a verified email in SES.
    """

    text += """

-- 
Corona Impf-o-mat
"""
    msg = create_multipart_message(SENDER, [RECIPIENT], title, text, html, attachments)
    ses_client = boto3.client('ses')  # Use your settings here
    return ses_client.send_raw_email(
        Source=SENDER,
        Destinations=[RECIPIENT],
        RawMessage={'Data': msg.as_string()}
    )


def set_chrome_options():
    """Sets chrome options for Selenium.
    Chrome options for headless browser is enabled.
    """
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0.3 Safari/605.1.15"
    chrome_options.add_argument(f'user-agent={user_agent}')
    chrome_prefs = dict()
    chrome_options.experimental_options["prefs"] = chrome_prefs
    chrome_prefs["profile.default_content_settings"] = {"images": 2}
    return chrome_options


def screenshot(driver, filename=None):
    global screenshot_index
    if filename is None:
        filename = f'screenshot_{screenshot_index}'

    driver.save_screenshot(f'{OUT_PATH}/{filename}.png')
    screenshot_index += 1


def get_timestamp():
    tz = dateutil.tz.gettz('Europe/Berlin')
    return datetime.datetime.now(tz)


def get_url(code, postal_code, url, vaccine_code):
    if code:
        return url.format(code=code, postal_code=postal_code, vaccine_code=vaccine_code)
    else:
        return f'{url}impftermine/service?plz={postal_code}'


def write_file(filename, text):
    file = open(f'{OUT_PATH}/{filename}', 'w')
    file.write(text)
    file.close()


def get_process_script():
    file = open(f'process.js')
    content = file.read()
    file.close()
    return content


def fetch_json_data(driver: WebDriver):
    output = driver.execute_async_script(get_process_script(), 'get_ersttermin_json')
    write_file('ersttermin.json', output)

    output = driver.execute_async_script(get_process_script(), 'get_vaccination_list_json')
    write_file('vaccination-list.json', output)

    output = driver.execute_async_script(get_process_script(), 'get_version')
    write_file('version.txt', output)


def process(code, postal_code, url, vaccine_code, address):
    chrome_options = set_chrome_options()
    driver = webdriver.Chrome(options=chrome_options)

    web_url = get_url(code=code, postal_code=postal_code, url=url, vaccine_code=vaccine_code)

    print(get_timestamp(), end=' ', flush=True)

    try:
        driver.get(web_url)

        time.sleep(1)

        # we will take screenshots from time to time, this being the initial one
        screenshot(driver)

        # check if the page is currently in maintenance mode
        if "Wartungsarbeiten" in driver.page_source:
            print('site is currently in maintenance mode')
            return False

        if code:
            # check if the challenge validation page is the current one (this should be the case, anyway)
            if "Challenge Validation" in driver.title:
                timeout_sec = 60
                timeout_after = datetime.datetime.now() + datetime.timedelta(seconds=timeout_sec)
                # wait for the "processing" page to disappear (we will be redirected to somewhere else after 30s
                while "Challenge Validation" in driver.title:
                    print('.', end='')
                    time.sleep(3)
                    if datetime.datetime.now() > timeout_after:
                        raise Error(f'Timeout in the "Challenge Validation" step has occurred (timeout={timeout_sec}s)')

                screenshot(driver)
                print(' ', end='')

            # dismiss the cookie banner, else we will not be able to click on stuff behind it
            if "Cookie Hinweis" in driver.page_source:
                driver.find_element_by_class_name("cookies-info-close").click()
                time.sleep(2)
                screenshot(driver)

            driver.find_element_by_xpath("//label[@class='ets-radio-control']").click()
            time.sleep(1)
            screenshot(driver)

            codes = code.split('-')
            driver.find_element_by_xpath(f"//input[@name='ets-input-code-0']").send_keys(codes[0])
            driver.find_element_by_xpath(f"//input[@name='ets-input-code-1']").send_keys(codes[1])
            driver.find_element_by_xpath(f"//input[@name='ets-input-code-2']").send_keys(codes[2])

            time.sleep(2)
            screenshot(driver)

            driver.find_element_by_css_selector('app-corona-vaccination-yes > form > div:nth-child(3) > button').click()
            time.sleep(1)
            screenshot(driver)

            # fetch_json_data(driver)

            # now we do re-fetch the url for weird reasons..
            driver.get(web_url)
            time.sleep(3)
            screenshot(driver)

            # now we should see a page with a "Onlinebuchung für Ihre Corona-Schutzimpfung" text
            if "Onlinebuchung" not in driver.page_source:
                raise Error(f'was expecting to see "Onlinebuchung" but this string was not found')

            driver.find_element_by_xpath("//button[@data-target='#itsSearchAppointmentsModal']").click()
            time.sleep(3)
            screenshot(driver)

            # dismiss the cookie banner, else we will not be able to click on stuff behind it
            if "Cookie Hinweis" in driver.page_source:
                driver.find_element_by_class_name("cookies-info-close").click()
                time.sleep(1)

            success = False

            if "leider keine Termine" in driver.page_source:
                text = driver.find_element_by_class_name("ets-search-no-results").text
                write_file('no-appointments-text.txt', text)
                print(f'no appointments available')

            else:
                if "Gefundene Termine" not in driver.page_source:
                    raise Error(f'was expecting to see "Gefundene Termine" but this string was not found')

                screenshot(driver)
                driver.find_element_by_class_name('ets-slot-button').click()
                time.sleep(3)
                print(f'Success: at least one appointment found, I will try too book the first one..')
                write_file('form.html', driver.page_source)

                salutation = address['salutation']
                driver.find_element_by_xpath(f"//input[@name='salutation'][@value='{salutation}'").click()
                driver.find_element_by_xpath(f"//input[@name='firstname']").send_keys(address['surname'])
                driver.find_element_by_xpath(f"//input[@name='lastname']").send_keys(address['name'])
                driver.find_element_by_xpath(f"//input[@name='plz']").send_keys(address['postal_code'])
                driver.find_element_by_xpath(f"//input[@name='city']").send_keys(address['city'])
                driver.find_element_by_xpath(f"//input[@formcontrolname='street']").send_keys(address['street'])
                driver.find_element_by_xpath(f"//input[@formcontrolname='housenumber']").send_keys(address['street_no'])
                driver.find_element_by_xpath(f"//input[@name='name='phone']").send_keys(address['phone'])
                driver.find_element_by_xpath(f"//input[@name='notificationReceiver']").send_keys(address['email'])
                screenshot(driver)

                time.sleep(1)
                driver.find_element_by_xpath(f"//button[@type='submit']").click()

                time.sleep(3)
                write_file('after-submit.html', driver.page_source)

                success = True

            screenshot(driver)
            return success

        else:
            # dismiss the cookie banner, else we will not be able to click on stuff behind it
            if "Cookie Hinweis" in driver.page_source:
                driver.find_element_by_class_name("cookies-info-close").click()
                time.sleep(3)
            screenshot(driver)

            # now we should see a page with a "Wurde Ihr Anspruch auf .." text
            if "Wurde Ihr Anspruch" not in driver.page_source:
                raise Error(f'was expecting to see "Wurde Ihr Anspruch" but this string was not found')

            # click on "Nein"
            driver.find_element_by_css_selector('app-corona-vaccination > div:nth-child(2) > div > div > label:nth-child(2) > span').click()
            # wait some time
            time.sleep(5)
            screenshot(driver)

            if "Es wurden keine freien" in driver.page_source:
                print(f'no appointments available (1)')
                return False

            if "Folgende Personen" not in driver.page_source:
                raise Error(f'was expecting to see "Folgende Personen" but this string was not found')

            if "Gehören Sie" not in driver.page_source:
                raise Error(f'was expecting to see "Gehören Sie..." but this string was not found')

            driver.find_element_by_css_selector('app-corona-vaccination > div:nth-child(3) > div > div > div > div.ets-login-form-section.in > div > app-corona-vaccination-no > form > div.form-group.d-flex.justify-content-center > div > div > label:nth-child(1) > span').click()

            age="71"
            driver.find_element_by_xpath(f"//input[@name='age']").send_keys(age)
            time.sleep(2)
            screenshot(driver)

            driver.find_element_by_css_selector('app-corona-vaccination-no > form > div:nth-child(4) > button').click()
            time.sleep(1)
            screenshot(driver)

            if "Es wurden keine freien Termine" in driver.page_source:
                print(f'no appointments available (2)')
                return False

            write_file('page.html', driver.page_source)
            print(f'Success: saved page source to page.html..')
            return True

    except Error as error:
        print(error)
        write_file('console.log', json.dumps(driver.get_log('browser')))

    except Exception as e:
        print(e)
        ts_string = get_timestamp().strftime('%Y%m%d%H%M%S')
        write_file(f'error-{ts_string}-console.log', json.dumps(driver.get_log('browser')))
        print(f"""got an error while trying to parse the page.
Will save the screenshot and page source to error-{ts_string}-*""")
        print(e)
        screenshot(driver, f'error-{ts_string}-screenshot')
        write_file(f'error-{ts_string}-pagesource.html', driver.page_source)

        files = glob.glob(f'{OUT_PATH}/error-{ts_string}*')
        if RECIPIENT:
            send_mail('Corona Impf-o-mat :: Error',
                      f"""There were errors while interacting with the URL

{web_url}

{e}
""",
                      None,
                      files)

            for file in files:
                os.remove(file)

        return False

    finally:
        write_file('all-cookies.json', json.dumps(driver.get_cookies()))
        driver.close()


def remove_screenshot_files():
    global screenshot_index

    files = glob.glob(f'f{OUT_PATH}/screenshot_*.*')
    for f in files:
        os.remove(f)
    screenshot_index = 1


def get_config(config_file):
    with open(config_file) as file:
        data = yaml.full_load(file)
        return data


def main():
    parser = argparse.ArgumentParser(description='Corona Impf-o-mat')
    parser.add_argument('--config', help="Path to the configuration file. See documentation for details.", default="config.yml")
    parser.add_argument('--retry', help="Retry time in seconds, 0 to disable", type=int, default=0)
    parser.add_argument('--test-mail', help="Just send a mail for testing")

    args = parser.parse_args()

    if args.test_mail:
        recipient = args.test_mail
        print(f'Will send an email to {recipient}.')
        send_mail('Test Mail',
                  f"""This is just a test.
                                    
If you can read this text, everything is just fine!""",
                  None,
                  None)
        sys.exit()

    config_file = os.path.join(os.path.dirname(__file__),  '..', args.config)
    config = get_config(config_file)

    while True:
        for item, doc in config.items():
            if item == 'parties':
                for party in doc:
                    web_url = get_url(code=party['code'],
                                      postal_code=party['address']['postal_code'],
                                      url=party['url'],
                                      vaccine_code=party['vaccine_code'])

                    print(f'Using URL: {web_url}')
                    remove_screenshot_files()
                    try:
                        success = process(party['code'], party['address']['postal_code'], party['url'], party['vaccine_code'], party['address'])
                        if success:
                            send_mail(
                                f'Corona Impf-o-mat :: Notification',
                                f"""Corona vaccines are currently available, see the attached screenshots.
            
            To book an appointment, use this URL:
            
            <{web_url}>
            
            """,
                                None,
                                glob.glob(f'{OUT_PATH}/*.*'))

                    except Exception as e:
                        print(f'processing error: {e}')

                    if args.retry == 0:
                        break

                    if success:
                        print(f'Exit on purpose after the first successful attempt')
                        break

                    time.sleep(args.retry)


if __name__ == '__main__':
    main()
