type TelecomArticle = Record<string, unknown>

const mobileNetworkArticles: TelecomArticle[] = [
  {
    title: "Phone shows SOS only after SIM swap",
    article_id: "TEL-MOB-001",
    category: "mobile-network",
    product: "consumer wireless",
    summary:
      "Troubleshoot a line that shows SOS only, Emergency Calls Only, or No Service after moving a SIM into another handset.",
    symptoms: [
      "status bar shows SOS only",
      "outbound calls fail",
      "mobile data is unavailable",
    ],
    common_causes: [
      "device is carrier locked",
      "old eSIM profile is still active",
      "IMEI is not fully provisioned on the line",
    ],
    resolution_steps: [
      "Confirm the handset is unlocked and supports the carrier bands.",
      "Toggle airplane mode for 30 seconds and restart the phone.",
      "Delete inactive eSIM plans and reset network settings.",
      "If service does not return within 15 minutes, reprovision the line and verify the IMEI on the account.",
    ],
    keywords: ["SOS only", "SIM swap", "IMEI update", "no service"],
    updated_at: "2026-03-18",
  },
  {
    title: "5G icon missing even though coverage is available",
    article_id: "TEL-MOB-002",
    category: "mobile-network",
    product: "consumer wireless",
    summary:
      "Use this article when a customer expects 5G service but the device stays on LTE or 4G in an area marked as 5G coverage.",
    symptoms: [
      "customer sees LTE instead of 5G",
      "speed tests are slower than expected",
    ],
    common_causes: [
      "plan does not include 5G access",
      "SIM card is too old for 5G provisioning",
      "preferred network type is set to LTE only",
    ],
    resolution_steps: [
      "Verify the line is on a 5G-capable plan.",
      "Check the preferred network mode and change it to 5G Auto or NR/LTE.",
      "Refresh network provisioning and confirm the SIM is current.",
      "If all checks pass, recommend a SIM replacement or eSIM reprovisioning.",
    ],
    keywords: ["5G not working", "LTE only", "network mode"],
    updated_at: "2026-02-27",
  },
  {
    title: "International roaming enabled but data will not connect",
    article_id: "TEL-MOB-004",
    category: "mobile-network",
    product: "consumer wireless",
    summary:
      "Resolve cases where voice roaming works abroad but mobile data stays disconnected or a customer cannot attach to a partner network.",
    symptoms: [
      "calls work but data does not",
      "device shows bars but no internet",
    ],
    common_causes: [
      "data roaming is disabled on the device",
      "the roaming pass is missing or expired",
      "APN settings were overwritten by a local SIM",
    ],
    resolution_steps: [
      "Confirm international roaming and any travel passes are active on the line.",
      "Enable data roaming in device settings and reboot once.",
      "Reset APN values to the carrier defaults.",
      "Try manual network selection using a preferred roaming partner.",
    ],
    keywords: ["roaming data", "travel pass", "APN reset"],
    updated_at: "2026-03-04",
  },
]

const deviceAndMessagingArticles: TelecomArticle[] = [
  {
    title: "Activate eSIM on iPhone with QR code",
    article_id: "TEL-DEV-001",
    category: "device-setup",
    product: "consumer wireless",
    summary:
      "Guide for activating a new or replacement iPhone with an eSIM QR code or manual SM-DP+ address entry.",
    symptoms: [
      "customer is moving from physical SIM to eSIM",
      "camera cannot scan the QR code",
    ],
    common_causes: [
      "old cellular plan is still installed",
      "the QR code was already consumed",
      "device is not connected to Wi-Fi during setup",
    ],
    resolution_steps: [
      "Connect the iPhone to Wi-Fi before starting eSIM setup.",
      "Remove inactive or duplicate cellular plans.",
      "Scan the QR code or use manual entry for SM-DP+ if scanning fails.",
      "If activation is still pending, generate a fresh eSIM profile and resend the QR code.",
    ],
    keywords: ["eSIM activation", "QR code", "iPhone setup"],
    updated_at: "2026-02-02",
  },
  {
    title: "MMS messages fail on Android after APN changes",
    article_id: "TEL-DEV-002",
    category: "messaging",
    product: "consumer wireless",
    summary:
      "Troubleshoot picture messages that stop sending or downloading on Android after APN edits or device restores.",
    symptoms: [
      "text messages work but pictures fail",
      "group MMS messages do not arrive",
    ],
    common_causes: [
      "MMSC or MMS proxy fields are wrong",
      "mobile data is disabled",
      "preferred APN switched to a non-carrier profile",
    ],
    resolution_steps: [
      "Confirm mobile data is on.",
      "Reset APN settings to carrier default values.",
      "Turn off Wi-Fi temporarily and retry sending a small image.",
      "If MMS still fails, verify MMSC values from the support matrix.",
    ],
    keywords: ["MMS not sending", "picture messages", "APN settings"],
    updated_at: "2026-03-08",
  },
  {
    title: "Short code and OTP texts are not arriving",
    article_id: "TEL-DEV-003",
    category: "messaging",
    product: "consumer wireless",
    summary:
      "Support article for customers who can receive normal SMS but do not receive one-time passwords, bank codes, or other short code traffic.",
    symptoms: [
      "bank verification texts never arrive",
      "two-factor authentication codes are delayed",
    ],
    common_causes: [
      "line has spam blocking or parental controls enabled",
      "short code was previously opted out with STOP",
      "recent port-in has not fully propagated",
    ],
    resolution_steps: [
      "Check account-level spam filters and message blocking features.",
      "Ask the customer to text START or UNSTOP if the sender supports re-enrollment.",
      "Verify whether the line was ported in within the last 72 hours.",
      "Test with multiple known short code senders to isolate the issue.",
    ],
    keywords: ["OTP", "2FA", "short code", "verification text"],
    updated_at: "2026-01-30",
  },
]

const billingAndInternetArticles: TelecomArticle[] = [
  {
    title: "Port-in delayed because account details do not match",
    article_id: "TEL-BIL-001",
    category: "porting",
    product: "consumer wireless",
    summary:
      "Resolve a mobile number transfer that is delayed because the losing carrier rejected the request due to account number, transfer PIN, or ZIP code mismatch.",
    symptoms: [
      "customer says transfer is stuck pending",
      "temporary number still active",
    ],
    common_causes: [
      "wrong account number",
      "incorrect transfer PIN",
      "billing ZIP does not match the losing carrier record",
    ],
    resolution_steps: [
      "Verify the account number exactly as shown on the prior carrier bill or app.",
      "Regenerate the transfer PIN if the existing PIN may have expired.",
      "Confirm the billing ZIP code and account holder name are an exact match.",
      "Resubmit the port request and monitor the status tool for a fresh response.",
    ],
    keywords: ["number transfer", "port stuck", "transfer PIN"],
    updated_at: "2026-02-09",
  },
  {
    title: "Unexpected international roaming charges on the invoice",
    article_id: "TEL-BIL-003",
    category: "billing",
    product: "consumer wireless",
    summary:
      "Review roaming usage disputes and explain the difference between roaming passes, pay-per-use charges, and maritime or satellite usage exclusions.",
    symptoms: [
      "bill increased after overseas travel",
      "customer expected roaming pass coverage",
    ],
    common_causes: [
      "roaming pass was not active on all travel days",
      "usage occurred on a non-covered maritime or airline network",
      "secondary line on a dual-SIM device generated the charges",
    ],
    resolution_steps: [
      "Review the invoice detail and identify the visited network and usage dates.",
      "Check whether a roaming pass was active and when it started.",
      "Explain any exclusions for maritime, satellite, or in-flight networks.",
      "If the dispute appears valid, submit a billing review with the account notes and travel dates.",
    ],
    keywords: ["roaming charges", "travel bill", "billing dispute"],
    updated_at: "2026-03-21",
  },
  {
    title: "Fiber gateway shows red LOS light",
    article_id: "TEL-HOM-001",
    category: "home-internet",
    product: "fiber broadband",
    summary:
      "Troubleshoot a fiber modem or optical network terminal showing a red LOS light, blinking LOS alarm, or complete optical signal loss.",
    symptoms: ["internet offline", "LOS light is red or blinking"],
    common_causes: [
      "fiber patch cable is loose or bent",
      "building outage",
      "optical signal level is out of range",
    ],
    resolution_steps: [
      "Inspect the fiber patch cable and make sure it is not pinched or partially unplugged.",
      "Power cycle the ONT and gateway for 60 seconds.",
      "Check the outage dashboard for the serving area.",
      "If LOS stays red, schedule a field technician.",
    ],
    keywords: ["red LOS", "fiber down", "ONT alarm"],
    updated_at: "2026-01-19",
  },
]

const sampleFiles = [
  { name: "telecom-mobile-network.json", articles: mobileNetworkArticles },
  {
    name: "telecom-device-and-messaging.json",
    articles: deviceAndMessagingArticles,
  },
  {
    name: "telecom-billing-and-internet.json",
    articles: billingAndInternetArticles,
  },
]

export const buildTelecomSampleFiles = (): File[] =>
  sampleFiles.map(
    ({ name, articles }) =>
      new File([JSON.stringify(articles, null, 2)], name, {
        type: "application/json",
      }),
  )
