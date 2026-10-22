# Helix Precision - IT Security Standard (ITS-03, version 2.1)

## 1. Accounts and passwords
- Passwords must be at least 14 characters long and must not contain the user name.
- Passwords for normal accounts expire every 180 days; passwords for administrator accounts every 90 days.
- The last 10 passwords cannot be reused.
- After 5 failed sign-in attempts the account is locked for 30 minutes. The IT service desk can unlock it earlier after verifying the user's identity by a call-back to the registered mobile number.
- Shared accounts are not allowed, except machine-controller accounts on the shop floor, which are managed by the controls team.

## 2. Multi-factor authentication (MFA)
MFA is required for email, VPN, the ERP system and every administrator account. Authenticator app codes are preferred; SMS codes are allowed only for employees without a company smartphone. Hardware security keys are mandatory for domain administrators.

## 3. Remote access
Remote access is only through the company VPN from a company laptop. Personal devices may access email and Teams through the mobile app with MFA, but may not connect to the VPN. Remote access to shop-floor machine controllers is not allowed, except for Helix service engineers through the vendor access gateway, with a work order and the approval of the plant controls engineer for each session.

## 4. USB and removable media
USB storage devices are blocked on all office computers. On the shop floor, CNC programs are transferred through the DNC server; USB sticks may only be used on machines without a network connection (list kept by the controls team), and only sticks issued by IT, which are scanned at the kiosk before each use.

## 5. Software
Only software from the company software center may be installed. Requests for other software go through the IT service desk and need the approval of the information security officer (ISO). Freeware and browser extensions are treated the same way.

## 6. Data classification
| Class | Examples | Rules |
|---|---|---|
| Public | Product brochures | No restrictions |
| Internal | Procedures, this standard | Share only with employees and contractors under NDA |
| Confidential | Drawings, prices, supplier agreements | Encrypt when sent outside the company; no personal cloud storage |
| Restricted | Salary data, personal data, customer IP | Access on named-person basis; encrypt at rest and in transit; printing needs the data owner's approval |

## 7. Incidents
Report suspected phishing with the "Report phishing" button in Outlook. Report a lost or stolen laptop or phone to the IT service desk within 2 hours, and to the police within 24 hours if stolen. Security incidents involving personal data must be escalated to the data protection officer within 24 hours.

## 8. Exceptions
Exceptions to this standard need a written risk assessment and the approval of the chief information security officer (CISO). Exceptions are valid for at most 6 months and must be reviewed before they are renewed.
