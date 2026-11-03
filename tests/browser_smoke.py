"""Run against a dedicated development server, never a production database."""

import os
import shutil
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

url = os.getenv("TEST_BASE_URL", "http://127.0.0.1:8080")
username = os.environ["TEST_USERNAME"]
password = os.environ["TEST_PASSWORD"]
errors = []
import time

run_id = str(int(time.time()))
with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        executable_path=os.getenv("TEST_CHROMIUM") or shutil.which("chromium"),
        args=["--no-sandbox"],
    )
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url)
    page.get_by_label("Benutzername", exact=True).fill(username)
    page.get_by_label("Passwort", exact=True).fill(password)
    page.get_by_role("button", name="Anmelden", exact=True).click()
    page.get_by_role("heading", name="Alles bereit für den Einsatz?").wait_for()
    page.get_by_role("button", name="＋ Vorgang anlegen", exact=True).click()
    page.get_by_label("Bezeichnung", exact=True).fill("Browser Smoke Einsatz " + run_id)
    page.get_by_label("Standardausstattung", exact=True).check()
    page.get_by_role("button", name="Speichern", exact=True).click()
    page.get_by_role(
        "heading", name="Browser Smoke Einsatz " + run_id, exact=True
    ).wait_for()
    page.locator(".check-item input.done").first.check()
    page.get_by_role("button", name="＋ Kategorie", exact=True).click()
    page.get_by_label("Kategoriename", exact=True).fill("Browser Zusatz")
    page.get_by_role("button", name="Speichern", exact=True).click()
    page.get_by_role("heading", name="Browser Zusatz", exact=True).wait_for()
    page.get_by_role("button", name="Spez71", exact=True).click()
    page.get_by_role("button", name="＋ Spez71 hinzufügen").click()
    page.get_by_label("Bezeichnung", exact=True).fill("Browser Wareneingang")
    page.get_by_label("Originalvorlage").select_option(
        label="Spez71-04 · Wareneingangskontrolle · April 2020"
    )
    page.get_by_role("button", name="Speichern", exact=True).click()
    page.locator("textarea.pdf-field").first.wait_for()
    page.locator("textarea.pdf-field").first.fill("Passau")
    page.get_by_role("button", name="Entwurf speichern", exact=True).click()
    page.get_by_role("status").filter(has_text="Entwurf gespeichert").wait_for()
    page.screenshot(path="/tmp/kitchen-proof.png", full_page=True)
    page.get_by_role("button", name="Lebensmittel & Getränke").click()
    page.get_by_role("button", name="＋ Artikel", exact=True).click()
    page.get_by_label("Bezeichnung", exact=True).fill("Browser Wasser " + run_id)
    page.get_by_label("Einheit", exact=True).select_option("Liter")
    page.get_by_label("Sollbestand", exact=True).fill("3.125")
    page.get_by_role("button", name="Speichern", exact=True).click()
    row = page.locator("tr").filter(has_text="Browser Wasser " + run_id).first
    row.get_by_role("button", name="Buchen", exact=True).click()
    page.get_by_label("Buchungsart").select_option("in")
    page.get_by_label("Menge / gezählter Ist-Bestand (Liter)", exact=True).fill("1.125")
    page.get_by_label("Begründung", exact=True).fill("Browser Testlieferung")
    page.get_by_role("button", name="Speichern", exact=True).click()
    page.locator("tr").filter(has_text="Browser Wasser " + run_id).first.filter(
        has_text="1,125"
    ).wait_for()
    page.screenshot(path="/tmp/kitchen-stock.png", full_page=True)
    page.get_by_role("button", name="Temperaturkontrolle", exact=False).first.click()
    page.get_by_role("button", name="＋ Gerät", exact=True).click()
    page.get_by_label("Name", exact=True).fill("Browser Kühlschrank " + run_id)
    page.get_by_label("Standort", exact=True).fill("Küche")
    page.get_by_role("button", name="Speichern", exact=True).click()
    page.get_by_role("button", name="＋ Temperatur eintragen", exact=True).click()
    page.get_by_label("Temperatur °C", exact=True).fill("4.125")
    page.get_by_role("button", name="Unveränderbar speichern", exact=True).click()
    page.locator("tr").filter(has_text="4,125").first.wait_for()
    page.screenshot(path="/tmp/kitchen-temperatures.png", full_page=True)
    page.get_by_role(
        "button", name="Zeitraum als Spez71 exportieren", exact=True
    ).click()
    page.get_by_label("Von (lokale Zeitzone)", exact=True).fill(
        (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    )
    page.get_by_role("button", name="Nachweis erstellen", exact=True).click()
    page.get_by_role("button", name="Final Speichern", exact=True).wait_for()
    page.on("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Final Speichern", exact=True).click()
    page.locator(".final-badge").wait_for()
    assert page.locator(".pdf-field:not(:disabled)").count() == 0
    for name in [
        "Dateiablage",
        "Vorgangsvorlagen",
        "Spez71-Nachweise",
        "Archiv · 10 Jahre",
        "Benutzer & API",
        "OV-Konfiguration",
        "Änderungsprotokoll",
    ]:
        page.locator("nav").get_by_role("button", name=name, exact=False).click()
        page.wait_for_timeout(150)
        assert "Anfrage fehlgeschlagen" not in page.locator("#workspace").inner_text()
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator("nav").get_by_role(
        "button", name="Lebensmittel & Getränke", exact=False
    ).click()
    page.locator("#stock-table").wait_for()
    page.screenshot(path="/tmp/kitchen-mobile.png", full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
        "Mobile layout overflows viewport"
    )
    assert not errors, errors
    browser.close()
print(
    "Browser workflow passed: login, operation, checklist/category, Spez71, stock, device/reading, navigation, mobile."
)
