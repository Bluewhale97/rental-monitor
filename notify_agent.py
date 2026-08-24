import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import json
import os
import csv
from datetime import datetime

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = os.getenv("RENT_SENDER_EMAIL", "stanley.z4r@gmail.com")
SENDER_PASSWORD = os.getenv("RENT_SENDER_PASSWORD", "hbhkdmruaxfluebs")
RECEIVER_EMAILS = ["stanley.z4r@gmail.com"]

HISTORY_FILE = "sent_listings.json"
CSV_DATABASE = "rental_database.csv"

def load_sent_listings():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []

def save_sent_listings(sent_list):
    with open(HISTORY_FILE, "w") as f:
        json.dump(sent_list, f, indent=4)

def update_csv_database(new_listings):
    file_exists = os.path.exists(CSV_DATABASE)
    fieldnames = [
        "id", "name", "type", "price", "amenities", 
        "commute", "contact", "link", "first_seen_date"
    ]
    existing_rows = {}
    if file_exists:
        with open(CSV_DATABASE, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing_rows[row["id"]] = row

    today = datetime.now().strftime("%Y-%m-%d")
    for item in new_listings:
        item_id = item["id"]
        if item_id in existing_rows:
            existing_rows[item_id].update({
                "price": item.get("price", existing_rows[item_id]["price"]),
                "amenities": item.get("amenities", existing_rows[item_id].get("amenities", "")),
                "contact": item.get("contact", existing_rows[item_id].get("contact", "")),
                "link": item.get("link", existing_rows[item_id]["link"])
            })
        else:
            existing_rows[item_id] = {
                "id": item_id,
                "name": item.get("name"),
                "type": item.get("type"),
                "price": item.get("price"),
                "amenities": item.get("amenities", "In-unit laundry, Disposal"),
                "commute": item.get("commute"),
                "contact": item.get("contact", "N/A"),
                "link": item.get("link"),
                "first_seen_date": today
            }

    with open(CSV_DATABASE, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in existing_rows.values():
            writer.writerow(row)

def send_email_notification(subject, html_content, dry_run=False):
    if dry_run:
        print(f"[Dry Run] Email would be sent to: {RECEIVER_EMAILS}")
        return True

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SENDER_EMAIL
        msg["To"] = ", ".join(RECEIVER_EMAILS)
        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECEIVER_EMAILS, msg.as_string())
        print("Email sent successfully!")
        return True
    except Exception as e:
        print(f"Failed to send email: {e}")
        return False

def fetch_real_listings():
    """
    Modular data ingestion point. 
    Connect your scraper or API client here to dynamically pull real listings.
    Must return a list of dictionaries matching the schema.
    """
    listings = []
    
    # Example placeholder structure for future integration:
    # listings.append({
    #     "id": "unique_property_id",
    #     "name": "Community Name",
    #     "type": "Apartment Community",
    #     "price": "$2,800",
    #     "amenities": "In-unit laundry, Disposal",
    #     "commute": "~10 mins",
    #     "contact": "(555) 000-0000",
    #     "link": "https://example.com/property"
    # })
    
    return listings

def run_scan(listings_input, dry_run=False):
    if not listings_input:
        print("No listings found during this execution cycle.")
        return

    update_csv_database(listings_input)
    sent_listings = load_sent_listings()
    unsent_listings = [item for item in listings_input if item["id"] not in sent_listings]

    if not unsent_listings:
        print("No new unnotified listings.")
        return

    table_rows = ""
    for item in unsent_listings:
        table_rows += f"""
        <tr style="border-bottom: 1px solid #eaeaea;">
            <td style="padding: 10px; font-weight: bold; color: #333; font-size: 13px;">{item.get('name')}</td>
            <td style="padding: 10px; color: #555; font-size: 13px;">{item.get('type')}</td>
            <td style="padding: 10px; color: #2c7a7b; font-weight: bold; font-size: 13px;">{item.get('price')}</td>
            <td style="padding: 10px; color: #555; font-size: 13px;">{item.get('amenities', 'In-unit laundry, Disposal')}</td>
            <td style="padding: 10px; color: #555; font-size: 13px;">{item.get('commute')}</td>
            <td style="padding: 10px; color: #555; font-size: 13px;">{item.get('contact', 'N/A')}</td>
            <td style="padding: 10px; text-align: right; font-size: 13px;">
                <a href="{item.get('link')}" style="background-color: #3182ce; color: white; padding: 5px 10px; text-decoration: none; border-radius: 4px;">Link</a>
            </td>
        </tr>
        """

    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background-color: #f7fafc; margin: 0; padding: 20px;">
        <div style="max-width: 900px; margin: auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
            <h2 style="color: #2d3748; margin-top: 0; border-bottom: 2px solid #edf2f7; padding-bottom: 10px; font-size: 18px;">
                🎯 Rental Alert: {len(unsent_listings)} New Match(es)
            </h2>
            <table style="width: 100%; border-collapse: collapse; margin-top: 14px; margin-bottom: 15px;">
                <thead>
                    <tr style="background-color: #edf2f7; text-align: left;">
                        <th style="padding: 10px; color: #4a5568; font-size: 12px;">Community</th>
                        <th style="padding: 10px; color: #4a5568; font-size: 12px;">Type</th>
                        <th style="padding: 10px; color: #4a5568; font-size: 12px;">Price</th>
                        <th style="padding: 10px; color: #4a5568; font-size: 12px;">Amenities</th>
                        <th style="padding: 10px; color: #4a5568; font-size: 12px;">Commute</th>
                        <th style="padding: 10px; color: #4a5568; font-size: 12px;">Contact</th>
                        <th style="padding: 10px; color: #4a5568; font-size: 12px; text-align: right;">Action</th>
                    </tr>
                </thead>
                <tbody>
                    {table_rows}
                </tbody>
            </table>
            <p style="color: #a0aec0; font-size: 11px; text-align: center; margin-bottom: 0;">
                Automated Rental Intelligence System
            </p>
        </div>
    </body>
    </html>
    """

    subject = f"🎯 [Rental Radar] {len(unsent_listings)} New Property Match(es) Found!"
    
    if send_email_notification(subject, html_content, dry_run=dry_run):
        for item in unsent_listings:
            sent_listings.append(item["id"])
        save_sent_listings(sent_listings)

if __name__ == "__main__":
    live_listings = fetch_real_listings()
    run_scan(live_listings, dry_run=False)