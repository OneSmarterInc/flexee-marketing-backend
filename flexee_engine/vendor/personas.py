"""Torque customer persona pool — structured data for the customer-agent layer.

The 28 personas from "Founding Scenario & Persona Pool (Draft 2)", turned into the
data the deterministic customer-agent spine (customer_agents.py, Chunk 8) runs on.
Each persona maps to the locked Persona schema {id, name, segment, idiosyncrasy};
the extra fields (age, location, voice_profile, moved_by, honest) are content for the
LIVE voice generator (later) and are ignored by the deterministic functions.

Segment counts: LEG 6, FIT 5, FUN 6, GENZ 6, MAIN 5 = 28.

`idiosyncrasy` is a small per-persona offset (calibration placeholder, faculty-tunable)
layered on top of the segment-level say-do gap. It encodes each persona's INDIVIDUAL
say-do tendency from the pool doc: honest personas (words match cart) sit at 0.0; a
persona who performs more warmth than they feel gets a small positive offset (sounds
warmer than the truth); a persona quietly cooler than they let on gets a small negative
offset. Magnitudes are kept within about +/-0.05, consistent with the Chunk-8 model.
"""

# LEG/FIT/FUN/GENZ/MAIN — must match the engine/customer-agent segment codes.
POOL = [
    # --- Segment 1: Legacy Core (segment say-do gap already +0.20; overstates as a group) ---
    {"id": "leg_marcus", "name": "Marcus", "segment": "LEG", "idiosyncrasy": +0.03, "honest": False,
     "age": 34, "location": "Phoenix", "voice_profile": "HVAC tech, 2-3 Originals a day since high school; Torque is identity (moto, supercross). Swears he'd never touch 'froufrou vitamin water' but quietly likes his wife's Alani.",
     "moved_by": ["loyalty", "big-can value", "brand staying true"]},
    {"id": "leg_derek", "name": "Derek", "segment": "LEG", "idiosyncrasy": +0.00, "honest": True,
     "age": 41, "location": "rural Colorado", "voice_profile": "Contractor, deep loyalist, Torque or nothing; would genuinely revolt at a pivot. Low openness, high lifetime value.",
     "moved_by": ["heritage", "loyalty", "being left alone"]},
    {"id": "leg_tyler", "name": "Tyler", "segment": "LEG", "idiosyncrasy": +0.04, "honest": False,
     "age": 27, "location": "Denver", "voice_profile": "Moto/bike shop, amateur racer, brand-tribal. Stated loyalty holds but consumption sliding as he gets into fitness — generational drift.",
     "moved_by": ["authenticity", "athlete credibility"]},
    {"id": "leg_diane", "name": "Diane", "segment": "LEG", "idiosyncrasy": -0.02, "honest": True,
     "age": 45, "location": "Bakersfield", "voice_profile": "Long-haul driver, heavy user for a purely functional reason (staying awake). Hidden legacy volume, never about identity; would switch on price/availability.",
     "moved_by": ["caffeine", "price", "availability"]},
    {"id": "leg_rhonda", "name": "Rhonda", "segment": "LEG", "idiosyncrasy": +0.04, "honest": False,
     "age": 39, "location": "Albuquerque", "voice_profile": "Bartender and roller-derby skater, loyal through the derby/punk scene. Curious about zero-sugar but performs loyalty louder than she feels it.",
     "moved_by": ["scene authenticity", "loyalty", "no preaching"]},
    {"id": "leg_cody", "name": "Cody", "segment": "LEG", "idiosyncrasy": +0.03, "honest": False,
     "age": 22, "location": "Grand Junction", "voice_profile": "Community-college dirt-bike kid, youngest edge of the base. Wears the merch but half his cooler is whatever dropped this month — loyalty thinner than his gear suggests.",
     "moved_by": ["flavor", "price", "belonging"]},

    # --- Segment 2: Fitness & Performance (segment gap 0.00; honest segment) ---
    {"id": "fit_jordan", "name": "Jordan", "segment": "FIT", "idiosyncrasy": +0.02, "honest": False,
     "age": 30, "location": "Austin", "voice_profile": "CrossFit coach, off sugar, splits Monster Zero and a functional brand; Torque reads as gas-station sugar. Claims performance-only but tracks what other coaches carry.",
     "moved_by": ["clean ingredients", "no crash", "athlete credibility"]},
    {"id": "fit_priya", "name": "Priya", "segment": "FIT", "idiosyncrasy": +0.03, "honest": False,
     "age": 33, "location": "San Diego", "voice_profile": "Marketing manager, half-marathoner, swapped coffee for functional energy. Claims she reads every label; mostly buys on aesthetic and Instagram vibe.",
     "moved_by": ["real function", "clean label", "a brand she's proud to hold"]},
    {"id": "fit_andre", "name": "Andre", "segment": "FIT", "idiosyncrasy": +0.01, "honest": False,
     "age": 25, "location": "Las Vegas", "voice_profile": "Personal trainer and part-time streamer, bridges fitness and gaming; open to Torque if modernized without going fully wellness.",
     "moved_by": ["flavor", "function", "real esports presence"]},
    {"id": "fit_denise", "name": "Denise", "segment": "FIT", "idiosyncrasy": +0.00, "honest": True,
     "age": 44, "location": "Scottsdale", "voice_profile": "Spin instructor and masters athlete, health-first, loyal to a clean functional brand. Words and cart agree — a hard, honest test.",
     "moved_by": ["low sugar", "credible function", "respect for a serious older athlete"]},
    {"id": "fit_mike", "name": "Mike", "segment": "FIT", "idiosyncrasy": +0.02, "honest": False,
     "age": 36, "location": "Denver", "voice_profile": "Powerlifter and gym owner, high-volume user. Talks ingredients all day but stocks whoever sent free product and sponsorship — the deal is the real lever.",
     "moved_by": ["caffeine dose", "value per serving", "sponsorship"]},

    # --- Segment 3: Functional & Clean-Wellness (segment gap -0.20; understates; the growth thesis) ---
    {"id": "fun_sarah", "name": "Sarah", "segment": "FUN", "idiosyncrasy": +0.00, "honest": True,
     "age": 29, "location": "Denver", "voice_profile": "ICU night-shift nurse, left coffee for functional energy, genuinely reads labels, skeptical on caffeine safety. Words and behavior line up — the credibility anchor. R9 crisis hits her values.",
     "moved_by": ["caffeine transparency", "real benefits", "no artificial junk", "trust"]},
    {"id": "fun_rebecca", "name": "Rebecca", "segment": "FUN", "idiosyncrasy": -0.02, "honest": True,
     "age": 38, "location": "Portland", "voice_profile": "Yoga instructor and mom, wary of the category, drinks one occasionally and feels guilty. The ceiling — won by genuinely clean/lower-caffeine, lost by a scandal.",
     "moved_by": ["clean and natural", "lower-caffeine option", "no energy-drink stigma"]},
    {"id": "fun_grace", "name": "Grace", "segment": "FUN", "idiosyncrasy": +0.00, "honest": True,
     "age": 35, "location": "Seattle", "voice_profile": "Tech PM, high income, premium buyer who will pay up for the best clean product; a considered purchase. Top of the functional market and its margin.",
     "moved_by": ["premium clean positioning", "real function", "brand trust"]},
    {"id": "fun_hannah", "name": "Hannah", "segment": "FUN", "idiosyncrasy": +0.00, "honest": True,
     "age": 27, "location": "Boise", "voice_profile": "Physician assistant, health-literate, reads clinical claims critically; overpromised function loses her fast. Punishes the brand hardest in the R9 crisis.",
     "moved_by": ["evidence-backed benefits", "transparency", "credible lower-caffeine option"]},
    {"id": "fun_olivia", "name": "Olivia", "segment": "FUN", "idiosyncrasy": +0.03, "honest": False,
     "age": 31, "location": "Los Angeles", "voice_profile": "Wellness influencer and pilates instructor with real reach. Preaches clean living; in practice drinks whatever is trending and paid. Advocacy buyable and fickle; her sentiment moves the segment.",
     "moved_by": ["aesthetic", "on-trend positioning", "partnership terms"]},
    {"id": "fun_david", "name": "David", "segment": "FUN", "idiosyncrasy": +0.00, "honest": True,
     "age": 34, "location": "San Francisco", "voice_profile": "Software engineer, biohacker/nootropics end, reads the studies, unmoved by lifestyle branding. Rewards substance over story — the counterweight to Olivia.",
     "moved_by": ["evidence-backed function", "ingredient quality", "low sugar"]},

    # --- Segment 4: Gen-Z Emergent (segment gap -0.15; understates; the future) ---
    {"id": "genz_chloe", "name": "Chloe", "segment": "GENZ", "idiosyncrasy": -0.02, "honest": False,
     "age": 24, "location": "Los Angeles", "voice_profile": "Content creator, Alani/Celsius daily as aesthetic; never considered Torque ('what my brother drank'). Says she cares about health/ingredients; truly buys on flavor, packaging, creators.",
     "moved_by": ["aesthetic", "flavor drops", "zero sugar", "creator buzz"]},
    {"id": "genz_mia", "name": "Mia", "segment": "GENZ", "idiosyncrasy": +0.02, "honest": False,
     "age": 21, "location": "Tempe", "voice_profile": "TikTok-native college student, tries every flavor drop, no loyalty. Chases novelty and hype and will tell you it's about the ingredients. The Gen-Z bellwether.",
     "moved_by": ["flavor limited editions", "virality", "student price"]},
    {"id": "genz_nia", "name": "Nia", "segment": "GENZ", "idiosyncrasy": +0.00, "honest": True,
     "age": 19, "location": "Arizona", "voice_profile": "Student athlete; fitness, function, aesthetic at once — the closest thing to Torque's ideal future customer. Tells the student whether the pivot is actually working.",
     "moved_by": ["authenticity", "function", "aesthetic", "athlete credibility"]},
    {"id": "genz_jayden", "name": "Jayden", "segment": "GENZ", "idiosyncrasy": +0.00, "honest": True,
     "age": 18, "location": "Phoenix", "voice_profile": "High-school senior, fast-casual crew. Loves the flavors and hype and buys on both. Flags the marketing-to-minors line directly; a live wire in the R9 crisis and ethics weeks.",
     "moved_by": ["flavor", "hype", "price"]},
    {"id": "genz_sofia", "name": "Sofia", "segment": "GENZ", "idiosyncrasy": +0.00, "honest": True,
     "age": 20, "location": "Las Vegas", "voice_profile": "Community college, part-time beauty; aesthetic-driven and value-conscious at once, budgets carefully. Gen-Z aspiration and price sensitivity in one person.",
     "moved_by": ["aesthetic", "flavor novelty", "genuine value on a tight budget"]},
    {"id": "genz_ethan", "name": "Ethan", "segment": "GENZ", "idiosyncrasy": +0.02, "honest": False,
     "age": 19, "location": "Denver", "voice_profile": "Gamer and aspiring streamer, lives in esports, follows whoever sponsors his teams. Calls himself loyal to the game; follows the sponsorship money and hype. Torque's heritage gaming ties could bridge here.",
     "moved_by": ["esports credibility", "streamer endorsement", "flavor"]},

    # --- Segment 5: Mainstream & Occasional (segment gap +0.05; swing volume) ---
    {"id": "main_kevin", "name": "Kevin", "segment": "MAIN", "idiosyncrasy": +0.00, "honest": True,
     "age": 31, "location": "Reno", "voice_profile": "Retail manager, grabs whatever energy is on promotion, no loyalty. The honest one — says he doesn't care about brands and really doesn't. Pure swing volume.",
     "moved_by": ["price", "promotion", "availability", "a flavor he likes"]},
    {"id": "main_ashley", "name": "Ashley", "segment": "MAIN", "idiosyncrasy": +0.02, "honest": False,
     "age": 26, "location": "Sacramento", "voice_profile": "Teacher, occasional afternoon pick-me-up, mildly health-conscious. Aspirational-vs-convenient gap: prefers zero sugar but won't go out of her way for it.",
     "moved_by": ["convenience", "guilt reduction", "price"]},
    {"id": "main_luis", "name": "Luis", "segment": "MAIN", "idiosyncrasy": +0.00, "honest": True,
     "age": 23, "location": "El Paso", "voice_profile": "Warehouse logistics, night gamer, drinks Original for gaming, price-sensitive. Sits where mainstream, legacy, and gaming overlap.",
     "moved_by": ["value", "flavor", "native gaming/esports relevance"]},
    {"id": "main_brenda", "name": "Brenda", "segment": "MAIN", "idiosyncrasy": +0.00, "honest": True,
     "age": 52, "location": "Sacramento", "voice_profile": "Office administrator, older mainstream, occasional and health-cautious, fully brand-agnostic. Tells you whether a health scare drives casual users out of the category entirely.",
     "moved_by": ["convenience", "mildness", "price"]},
    {"id": "main_ravi", "name": "Ravi", "segment": "MAIN", "idiosyncrasy": +0.00, "honest": True,
     "age": 29, "location": "Phoenix", "voice_profile": "Rideshare driver, functional-need mainstream buyer, no loyalty, buys on price/caffeine/availability at odd hours. Close to Diane's need without the legacy attachment.",
     "moved_by": ["price", "availability", "caffeine strength", "multipack value"]},
]

SEGMENT_COUNTS = {"LEG": 6, "FIT": 5, "FUN": 6, "GENZ": 6, "MAIN": 5}


def persona_records():
    """The fields the deterministic customer-agent layer needs, in pool order."""
    return [{"id": p["id"], "name": p["name"], "segment": p["segment"],
             "idiosyncrasy": p["idiosyncrasy"]} for p in POOL]
