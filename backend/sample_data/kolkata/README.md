# Demo inventory (synthetic_demo_data, FAKE)

Synthetic, entirely fictional inventory for demos: **19 projects, 44 home types, 129 homes**
across Kolkata (New Town Action Area I, II and III, Rajarhat, Salt Lake Sector I and V, Howrah,
Ballygunge, Tollygunge, Behala, Garia, Joka, Kasba, Dum Dum, Baguiati), with offers, payment
plans and amenities. Every project name ends in "(Sample)" and every description starts with
`SAMPLE DATA (synthetic_demo_data)`. None of the projects, addresses, RERA numbers or prices are
real; never present them as real listings. The dashboard's Properties page shows a "Demo data"
notice while any of them is loaded.

Homes: 92 available, 14 booked, 12 sold, 11 blocked. 1 BHK from 28 lakh, 2 BHK from 42 lakh,
3 BHK from 68 lakh to 3 crore, 4 BHK, villas and penthouses up to 6.5 crore. Furnishing,
bathrooms and parking are in each project's description and amenities (the data model has no
separate columns for them).

- Load or refresh: `docker compose run --rm --no-deps backend python -m app.seed` (repeatable;
  it goes through the normal Data Center upload, validate, approve path).
- Regenerate the CSVs: `python sample_data/make_kolkata.py tests/fixtures/sample_inventory
  sample_data/kolkata` (from `backend/`).
- Replace with real inventory: upload the developer's CSVs (same columns) in the Data Center.
