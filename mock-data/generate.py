"""Generate fully-populated sample import files — one CSV per bulk-importable
entity in Hearth, every column filled with realistic data. Run this to (re)write
mock-data/imports/*.csv, which the app's Import screens accept as-is.

    python backend/mock-data/generate.py

Column order and valid values mirror each importer exactly:
  rooms          apps/rooms/views.py       (room type auto-created)
  tables         apps/pos/views.py         (branch optional)
  bar-tables     apps/pos/views.py
  menu-categories apps/pos/views.py
  menu-items     apps/pos/views.py         (diet: veg/nonveg/egg; station: active KitchenStation)
  ingredients    apps/inventory/views.py   (category + unit auto-created)
  suppliers      apps/procurement/views.py
  branches       apps/accounts/views.py    (edition: hotel/restaurant/both)
  employees      apps/hr/views.py          (department + role must already exist)
"""
import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "imports")

DATASETS = {
    "rooms.csv": (
        ["number", "room_type_code", "room_type_name", "base_rate", "floor"],
        [
            ["101", "STD", "Standard", "4500", "1"],
            ["102", "STD", "Standard", "4500", "1"],
            ["103", "STD", "Standard", "4500", "1"],
            ["201", "DLX", "Deluxe", "6500", "2"],
            ["202", "DLX", "Deluxe", "6500", "2"],
            ["301", "STE", "Suite", "9500", "3"],
            ["302", "STE", "Suite", "9500", "3"],
            ["401", "PRES", "Presidential Suite", "18000", "4"],
        ],
    ),
    "tables.csv": (
        ["name", "floor", "section", "seats", "branch"],
        [
            ["T1", "Ground", "AC Hall", "4", ""],
            ["T2", "Ground", "AC Hall", "4", ""],
            ["T3", "Ground", "AC Hall", "2", ""],
            ["T4", "Ground", "Non-AC", "6", ""],
            ["G1", "Garden", "Outdoor", "4", ""],
            ["G2", "Garden", "Outdoor", "4", ""],
            ["P1", "Terrace", "Poolside", "8", ""],
        ],
    ),
    "bar-tables.csv": (
        ["name", "section", "seats", "branch"],
        [
            ["BT1", "Bar Counter", "2", ""],
            ["BT2", "Bar Counter", "2", ""],
            ["BT3", "Lounge", "4", ""],
            ["BT4", "Lounge", "4", ""],
            ["BT5", "Rooftop Bar", "6", ""],
        ],
    ),
    "menu-categories.csv": (
        ["name", "sort_order", "is_bar"],
        [
            ["Starters", "1", "false"],
            ["Soups", "2", "false"],
            ["Mains", "3", "false"],
            ["Breads", "4", "false"],
            ["Rice & Biryani", "5", "false"],
            ["Desserts", "6", "false"],
            ["Beverages", "7", "false"],
            ["Cocktails", "8", "true"],
        ],
    ),
    "menu-items.csv": (
        ["name", "category", "price", "gst_rate", "diet", "station", "short_code"],
        [
            ["Paneer Tikka", "Starters", "320", "5", "veg", "kitchen", "PT"],
            ["Chicken 65", "Starters", "340", "5", "nonveg", "kitchen", "C65"],
            ["Masala Omelette", "Starters", "140", "5", "egg", "kitchen", "MO"],
            ["Tomato Soup", "Soups", "180", "5", "veg", "kitchen", "TS"],
            ["Chicken Biryani", "Rice & Biryani", "380", "5", "nonveg", "kitchen", "CB"],
            ["Veg Biryani", "Rice & Biryani", "320", "5", "veg", "kitchen", "VB"],
            ["Butter Naan", "Breads", "60", "5", "veg", "kitchen", "BN"],
            ["Paneer Butter Masala", "Mains", "340", "5", "veg", "kitchen", "PBM"],
            ["Gulab Jamun", "Desserts", "120", "5", "veg", "kitchen", "GJ"],
            ["Fresh Lime Soda", "Beverages", "90", "5", "veg", "kitchen", "FLS"],
        ],
    ),
    "ingredients.csv": (
        ["name", "category", "unit", "opening_stock", "min_stock_level",
         "reorder_level", "purchase_rate", "storage_location", "expiry_date"],
        [
            ["Basmati Rice", "Grains", "kg", "50", "10", "20", "90", "Dry Store", ""],
            ["Wheat Flour", "Grains", "kg", "35", "8", "15", "45", "Dry Store", ""],
            ["Chicken", "Meat", "kg", "30", "5", "15", "220", "Cold Room", "2026-08-10"],
            ["Paneer", "Dairy", "kg", "15", "3", "8", "320", "Cold Room", "2026-08-05"],
            ["Milk", "Dairy", "l", "40", "10", "20", "60", "Cold Room", "2026-08-03"],
            ["Tomato", "Vegetables", "kg", "25", "5", "12", "40", "Dry Store", ""],
            ["Onion", "Vegetables", "kg", "40", "10", "20", "35", "Dry Store", ""],
            ["Cooking Oil", "Oils", "l", "30", "5", "10", "140", "Dry Store", ""],
        ],
    ),
    "suppliers.csv": (
        ["name", "gstin", "contact", "payment_terms", "lead_time_days"],
        [
            ["Fresh Farms Pvt Ltd", "29ABCDS1234A1Z5", "+91 9800000011", "Net 30", "2"],
            ["Metro Cash & Carry", "29XYZAB5678B2Z9", "+91 9800000022", "Net 15", "1"],
            ["Coastal Seafoods", "29LMNOP9012C3Z1", "+91 9800000033", "Advance", "3"],
            ["Dairy Best", "29QRSTU3456D4Z2", "+91 9800000044", "Net 30", "1"],
            ["Spice World", "29VWXYZ7890E5Z3", "+91 9800000055", "Net 7", "4"],
        ],
    ),
    "branches.csv": (
        ["name", "code", "city", "state", "gstin", "edition", "invoice_prefix"],
        [
            ["Hearth Grand - Airport", "APT", "Bengaluru", "Karnataka", "29ABCDE1234F1Z5", "both", "APT-INV"],
            ["Hearth Grand - MG Road", "MGR", "Bengaluru", "Karnataka", "29ABCDE1234F1Z6", "both", "MGR-INV"],
            ["Hearth Bistro - Indiranagar", "IND", "Bengaluru", "Karnataka", "29ABCDE1234F1Z7", "restaurant", "IND-INV"],
        ],
    ),
    # Departments/designations are NOT auto-created — these values must already
    # exist as masters (the test harness / seed provides them).
    "employees.csv": (
        ["name", "department", "role", "country_code", "phone", "wage_type",
         "monthly_salary", "daily_rate", "weekly_rate", "branch"],
        [
            ["Anita Sharma", "Kitchen", "Cook", "+91", "9000000001", "monthly", "22000", "", "", ""],
            ["Ravi Kumar", "Kitchen", "Chef", "+91", "9000000002", "daily", "", "700", "", ""],
            ["Priya Nair", "Front Office", "Receptionist", "+91", "9000000003", "monthly", "25000", "", "", ""],
            ["Suresh Menon", "Housekeeping", "Housekeeping Attendant", "+91", "9000000004", "daily", "", "650", "", ""],
            ["Deepa Rao", "Bar", "Bartender", "+91", "9000000005", "weekly", "", "", "4500", ""],
        ],
    ),
}


def main():
    os.makedirs(OUT, exist_ok=True)
    for fname, (header, rows) in DATASETS.items():
        with open(os.path.join(OUT, fname), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)
        print(f"wrote imports/{fname}  ({len(rows)} rows, {len(header)} cols)")


if __name__ == "__main__":
    main()
