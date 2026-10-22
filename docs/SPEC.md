# SignalBloom Specification

## Persona

The main user is a non-technical office manager at a small New Zealand business with 10 to 75 staff and no dedicated IT person, for example a dental clinic with more than one location.
They rely on the network every day for payments, cloud services and talking to staff and customers, but they have little or no networking knowledge.
They want to know quickly whether the network is healthy, understand problems in plain language, and fix them without calling a technician.

## Tabs

**Network Health:** an overall health score from 0 to 100 with a colour indicator, built from bandwidth, latency, uptime, packet loss and error rate, plus trend charts for each.
**Security:** a separate security score and a list of security events, such as an unknown device joining the network. It does not affect the health score.
**Devices:** an inventory of every device on the network with its name, IP address, status and when it was last seen.
**AI Assistant:** plain-English explanations of problems and prioritised recommendations from Gemini, plus a chat to ask questions about the selected site.
**Account & Settings:** sites and their setup codes, per-site billing, users and their roles, email alert settings, and the audit log of AI recommendations and fixes.

## Roles

**Admin** can see all five tabs, add sites, manage users and billing, change alert settings, and approve AI-driven fixes.
**Staff** can see Network Health, Security, Devices and the AI Assistant. Staff cannot see billing, manage users or approve fixes.
Roles are enforced in the backend, not only hidden in the interface.

## Acceptance criteria

### Data collection
- AC-01: A local agent can register a site using a one-time setup code from the dashboard.
- AC-02: The agent sends latency, packet loss, uptime and device data to the cloud at least every 30 seconds.
- AC-03: The cloud rejects agent data sent without a valid site token.
- AC-04: The agent only makes outbound connections and does not open any port on the customer network.
- AC-05: If the cloud cannot be reached, the agent stores readings and sends them once the connection returns.

### Monitoring
- AC-06: The Network Health tab shows a health score from 0 to 100 that updates without refreshing the page.
- AC-07: A device that goes offline shows as offline in the Devices tab within one minute.
- AC-08: All technical terms on screen have a plain-English label, with the technical term available in a tooltip.

### Alerts and AI
- AC-09: An injected network fault creates an alert within two minutes.
- AC-10: Each alert shows a severity (critical, warning or info) and the reason it was raised.
- AC-11: Repeated alerts about the same issue are grouped into one alert to reduce alert fatigue.
- AC-12: Each critical or warning alert has a plain-English explanation and up to three recommendations, most important first.
- AC-13: The AI Assistant only uses data from the selected site and says so when it cannot answer.
- AC-14: The dashboard keeps working and shows a clear message if the AI service is unavailable.

### Fixes and accountability
- AC-15: The user can choose to fix a problem themselves or approve an AI-driven fix.
- AC-16: An approved fix is sent to the agent, and its result is shown in the dashboard.
- AC-17: Every AI recommendation, approval and fix result is recorded in the audit log.

### Access and security
- AC-18: Users must sign in before they can see any data.
- AC-19: Staff users cannot see billing or approve fixes, even by calling the backend directly.
- AC-20: Critical alerts send one email to users who have email alerts turned on.
- AC-21: No API keys or passwords are stored in the code or the GitHub repository.

### Deployment
- AC-22: The prototype runs on Google Cloud at a public HTTPS address.
- AC-23: A real network connected through the agent feeds live data to the deployed app.

## Open questions

- What counts as success for the prototype beyond passing these criteria? (formal success criteria)
- What speed, reliability and usability levels must the prototype meet? (non-functional requirements)
- Which features from the proposal are in the prototype, and which are future work? (scope boundaries)
- What are the exact steps for a new customer from sign-up to seeing their first data? (onboarding flow)
- Business targets in the proposal (99.95% uptime, 10,000+ endpoints, 80% to 90% AI accuracy) are for the full product, not the prototype. The team should confirm this.
