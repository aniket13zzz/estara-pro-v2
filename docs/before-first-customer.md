# Before the first paying customer

ESTARA is built at zero cost with free tiers and fake sample data. Everything below needs
money, a real business account or a professional review, and must be done **before real
customers talk to the agent**. Costs are rough guides only; check the provider's current
pricing.

| # | Item | Why | Who provides | Rough cost |
| --- | --- | --- | --- | --- |
| 1 | **Paid Gemini API key** (the client's own Google Cloud project with billing) | Free tier allows only a few requests a day, may use data to improve Google's models, and slows down under load | Client | Pay per use; small for one developer's call volume |
| 2 | **Full 33-conversation eval (including the privacy tricks)** on the paid key | Proves the agent's answers before go-live (only a 3-conversation smoke test runs on the free tier) | You | About USD 1–3 per full run (estimate) |
| 3 | **Vapi paid plan** in the client's own account | Free credit is for testing only | Client | Per call minute |
| 4 | **Indian phone number** connected to Vapi (call forwarding from the client's sales number, via an Indian SIP/telephony provider) | Callers dial a local number; needs provider KYC, which can take days to weeks | Client (KYC), you (setup) | Monthly rental + per minute |
| 5 | **Verified WhatsApp Business number** and **approved templates** (English and Hindi) in the client's Meta Business account | The free test number can only message a few pre-registered phones | Client (business verification), you (setup) | Meta per-conversation pricing |
| 6 | **Server** (small Linux VPS) with a domain and HTTPS | The backend must run 24/7; your PC is fine for demos only | Client | Monthly hosting |
| 7 | **Backup storage** off the server, encrypted | A restore must survive the server itself failing | Client | Small monthly storage |
| 8 | **Legal review** of the AI disclosure, call-recording notice, WhatsApp opt-in, and personal data handling (DPDP Act) | ESTARA documents requirements; it does not claim compliance | Client's lawyer | Professional fee |
| 9 | **Native Hindi review** of the scripts (`client/scripts/hi/`) and signal keywords | Natural, correct Hinglish | Client or a Hindi speaker | Time only |
| 10 | **The client's real, approved data** uploaded through the Data Center | The agent only quotes approved data | Client | Time only |
| 11 | **Pilot** (2–4 weeks, limited hours, a salesperson reviews every handoff) | Catch problems with real callers before full go-live | Client + you | Time only |

Until each item is done, it is marked **before first customer** in `docs/progress.md` and is
never presented as working.
