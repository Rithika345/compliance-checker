# Engineering Team Password Management Procedure

## Overview

This procedure describes how the Engineering team creates, protects, and manages account
passwords for systems and services we operate. It applies to all engineers, contractors, and
on-call staff who hold an account on any team-owned system, repository, or cloud console.

## Procedure

### Creating a password

All new account passwords must be created following the company's Password Construction
Guidelines (minimum length, complexity, and disallowed patterns). Every engineer must use a
separate, unique password for each work-related account they hold; a password used for a
work account must never also be used for a personal account.

Accounts that carry elevated or administrative privileges, such as `sudo` access on production
hosts or admin roles in cloud consoles, must use a password that is unique from the password on
that person's standard account. Multi-factor authentication is strongly recommended for these
elevated accounts and is enabled by default for all cloud console admin roles.

### Changing a password

We do not require engineers to rotate passwords on a fixed schedule. A password should only be
changed when there is a specific reason to believe it may have been compromised — for example,
after a suspected phishing attempt, a leaked-credential alert, or a lost device. When a change is
required, the new password must also conform to the Password Construction Guidelines.

### Protecting a password

Passwords must never be shared with anyone, including a manager, teammate, or on-call partner.
All passwords are treated as confidential information. Passwords must never be put into an
email, a support ticket, a chat message, or spoken over the phone to anyone, including IT
support staff helping with a login issue. Passwords may only be stored in the company's
approved password manager; engineers must not use a web browser's built-in "remember this
password" feature on any work device.

If an engineer suspects that a password has been compromised, they must report it to the
security channel immediately and change every password that may share the exposure.

### Multi-factor authentication

Multi-factor authentication is encouraged for all accounts wherever the underlying system
supports it, not only for work accounts but for personal accounts too.

### Building authentication into our own tools

Any internal tool or service our team builds that requires a login must authenticate individual
users rather than a shared group account, so that one person's access can be traced and revoked
without affecting anyone else. Our tools must never store a password in plain text or in any
other easily reversible form, and must never transmit a password unencrypted over the network.
Where a tool needs to let one person cover another's duties, it must do so through role or
permission assignment, not by having one person learn another's password.
