// UI chrome labels in English and Tamil. Data, plan text and messages are never
// machine-translated here; Tamil public messages are drafted by the agent and always
// reviewed by an officer. (Tamil UI labels: native-speaker review pending — see docs.)

export type Lang = "en" | "ta";

const en = {
  map: "Command map",
  sources: "Sources",
  public: "Public alerts",
  replay: "Replay",
  plans: "Plans",
  tracker: "Tracker",
  audit: "Audit log",
  runPlanner: "Run planner",
  approve: "Approve",
  edit: "Edit",
  reject: "Reject",
  WATCH: "WATCH",
  PREPARE: "PREPARE",
  ACT: "ACT",
  QUIET: "Quiet",
  doNow: "Do now",
  allClear: "All clear",
  lastScan: "Last scan",
  scanNow: "Scan now",
  login: "Officer login",
  logout: "Log out",
  districts: "Districts",
  expectedIn: "expected in",
  earliestOnset: "earliest onset",
  forecasts: "forecasts",
  stale: "STALE",
  degraded: "Degraded",
  disagree: "Sources disagree",
};

const ta: typeof en = {
  map: "கட்டளை வரைபடம்",
  sources: "தரவு மூலங்கள்",
  public: "பொது எச்சரிக்கைகள்",
  replay: "மறு இயக்கம்",
  plans: "திட்டங்கள்",
  tracker: "செயல் கண்காணிப்பு",
  audit: "தணிக்கை பதிவு",
  runPlanner: "திட்டமிடலை இயக்கு",
  approve: "ஒப்புதல்",
  edit: "திருத்து",
  reject: "நிராகரி",
  WATCH: "கண்காணிப்பு",
  PREPARE: "தயார்நிலை",
  ACT: "நடவடிக்கை",
  QUIET: "அமைதி",
  doNow: "இப்போதே செய்",
  allClear: "அனைத்தும் சரி",
  lastScan: "கடைசி ஸ்கேன்",
  scanNow: "இப்போது ஸ்கேன்",
  login: "அதிகாரி உள்நுழைவு",
  logout: "வெளியேறு",
  districts: "மாவட்டங்கள்",
  expectedIn: "எதிர்பார்க்கப்படுவது",
  earliestOnset: "முதல் தொடக்கம்",
  forecasts: "முன்னறிவிப்புகள்",
  stale: "பழைய தரவு",
  degraded: "குறைந்த தரம்",
  disagree: "மூலங்கள் முரண்படுகின்றன",
};

export const STRINGS: Record<Lang, typeof en> = { en, ta };
export type StringKey = keyof typeof en;
