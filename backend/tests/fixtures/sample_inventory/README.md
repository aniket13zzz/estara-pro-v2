# Sample inventory (FAKE DATA)

Clearly fake inventory for development, tests and demos. None of these projects, addresses,
RERA numbers or prices are real. Loaded by `make seed-sample` (`python -m app.seed`) through the
normal Data Center upload → validate → approve path, in this order:

projects → unit_types → units → price_lists → offers → payment_plans → amenities

Includes 2 BHK homes under 80 lakh (Sunrise Greens 2BHK-A from ₹72,00,000; Sunrise Heights
2BHK-C from ₹68,00,000) for the Stage 5 demo call.
