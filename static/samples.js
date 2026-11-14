// Demo cases shown on the home page. Names and companies are fictional; the laws they trigger are real.
// Shown as a 3-column grid: keep the count a multiple of 3.
//   emoji · category · title · location · question (the hook shown on the card) · text (the letter)
window.SAMPLES = [
  {
    emoji: "🌴", category: "Housing", title: "14.6% rent increase",
    location: "San Jose, California, USA",
    question: "Is a 14.6% hike with 30 days' notice even legal?",
    text: `NOTICE OF CHANGE IN TERMS OF TENANCY

To: Tenant(s) in possession, 1880 Alum Rock Ave Apt 7, San Jose, CA 95116

You are hereby notified that 30 days after service of this notice, your monthly rent will be increased from $2,400.00 to $2,750.00 (an increase of 14.6%), effective February 1, 2027.

The building is a 12-unit apartment complex built in 1986. This increase reflects current market rates.

Failure to pay the new amount will result in a 3-day notice to pay or quit.

Dated: December 20, 2026
Bayview Holdings LLC (fictional), Owner`,
  },
  {
    emoji: "✈️", category: "Travel", title: "Cancelled flight, €50 voucher",
    location: "Helsinki, Finland",
    question: "“Compensation is not applicable.” Is that true?",
    text: `Subject: Important change to your booking ABX7Q2

Dear passenger,

We regret to inform you that your flight NW512 Helsinki (HEL) – Barcelona (BCN) on 2 November 2026, scheduled departure 07:40, has been cancelled due to operational reasons.

You have been rebooked on NW518 departing 3 November 2026 at 14:20, arriving 18:35.

As a gesture of goodwill we are pleased to offer you a €50 travel voucher valid for 6 months. Please note that compensation is not applicable in this case. Meal expenses are not covered.

Kind regards,
Nordwind Air Customer Care (fictional carrier, EU-based)`,
  },
  {
    emoji: "🏠", category: "Housing", title: "18% rent hike or move out",
    location: "Helsinki, Finland",
    question: "Can a landlord override a 3% clause, in Finnish?",
    text: `Hyvä vuokralainen,

Kiinteistöjen ylläpitokulujen nousun vuoksi asuntonne (Kalliolanrinne 4 B 12, 00510 Helsinki) vuokra nousee 1.1.2027 alkaen 1 050 €/kk -> 1 240 €/kk.
Mikäli korotus ei sovi, pyydämme teitä muuttamaan pois 31.12.2026 mennessä.
Vuokrasopimus on toistaiseksi voimassa oleva, solmittu 1.3.2024. Sopimuksessa on vuosittainen 3 % korotusehto.

Ystävällisin terveisin,
Asunto Oy Kallioranta / isännöitsijä M. Virtanen`,
  },
  {
    emoji: "🔑", category: "Deposit", title: "£850 of deposit kept",
    location: "Manchester, England, UK",
    question: "Cleaning, repaint, carpet: which deductions stand?",
    text: `Hi,

Following the check-out inspection of 22 Ashworth Street on 31 October 2026, we will be retaining £850 from your £1,200 deposit:

- Professional cleaning of whole property: £300
- Full repaint of living room and bedroom (scuffs, picture hook holes): £450
- Replacement of carpet in hallway (worn): £100

You were a tenant for 4 years (Nov 2022 – Oct 2026). The remaining £350 will be returned within 30 days.

Regards,
Harbour Lettings (fictional)`,
  },
  {
    emoji: "🏥", category: "Healthcare", title: "Surprise $3,180 ER bill",
    location: "Austin, Texas, USA",
    question: "In-network hospital, out-of-network doctor: do I owe it?",
    text: `STATEMENT — BALANCE DUE

Patient account #448120
Date of service: 09/14/2026 — Emergency department visit, St. Cedar Medical Center (in-network with your plan)

Provider: Lone Star Emergency Physicians Group (out-of-network)
Total charges: $4,950.00
Insurance paid: $1,770.00
PATIENT BALANCE DUE: $3,180.00

This balance represents the difference between our billed charges and your insurer's allowed amount. Payment is due within 30 days. Unpaid balances may be referred to a collection agency.`,
  },
  {
    emoji: "💼", category: "Employment", title: "Final paycheck delayed",
    location: "Los Angeles, California, USA",
    question: "Can my ex-employer pay me “next payroll cycle”?",
    text: `Hi Jordan,

Following your termination on Friday, November 6, 2026, please note the following:

- Your final paycheck (including hours worked through Nov 6) will be issued on the next regular payroll date, November 25, 2026.
- Per company policy, accrued but unused vacation (64 hours) is forfeited upon termination and will not be paid out.
- A $150 deduction will be made from your final pay for the uniform and badge that were not returned.

Please contact HR with any questions.

Brightline Logistics Inc. (fictional), Human Resources`,
  },
];
