"""E-mail sending: Microsoft Graph API client, placeholder templates, and
bulk/invoice send orchestration.

Every recipient gets their own, separate `sendMail` call (never CC/BCC) --
that is both the privacy guarantee (no recipient ever sees another) and
the only way to attach a different PDF per recipient (invoice emails).
"""
