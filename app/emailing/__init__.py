"""E-mail sending: Microsoft Graph API client, placeholder templates, and
bulk/invoice send orchestration.

Every **contract party** gets their own, separate `sendMail` call (never
CC/BCC) -- that is both the privacy guarantee (no party ever sees another's
address) and the only way to attach a different PDF per recipient (invoice
emails).

A party is one `app.models.person.Person`, which for a couple holds two
email addresses. Those two do go into one message's `toRecipients`
together, and that is not a weakening of the rule: they are the two people
of one household, both parties to the same contract, who already know each
other's address. What the rule forbids -- and still forbids -- is putting
two *different* parties in one message. Keeping it at one message per party
also means there is exactly one outcome per party to record: an invoice is
either sent or not, never half sent to one partner.
"""
