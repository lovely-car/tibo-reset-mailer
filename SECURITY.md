# Reporting problems safely

Never post API keys, SMTP app passwords, recipient addresses, `.env` files or the private state database in public issues. Share the exception type and a redacted description instead. Rotate credentials with the provider if they were exposed.

Post and model text are untrusted inputs. Model outputs cannot choose recipients or arbitrary source URLs. Only configured TLS SMTP connections are supported. Runtime state contains email content and must stay private.

Use the repository's private vulnerability reporting feature when available. For parser/semantic accuracy issues, a public source permalink and a minimal synthetic reproduction are sufficient.
