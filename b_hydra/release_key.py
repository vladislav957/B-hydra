"""The public key that B-hydra Core releases are signed with.

This is a KEYRING, like Debian's `/etc/apt/trusted.gpg.d`. An update installs
only when the release manifest is signed with the corresponding PRIVATE key,
and that key lives offline with the project owner and never reaches the
repository.

⚠️ WHY THIS IS NEEDED AT ALL WHEN THERE IS HTTPS. TLS protects the channel to
GitHub — and nothing else. It says nothing about WHAT is sitting there: what
gets hijacked is not the channel but the account or the CI token, and then
the substituted file arrives over an honest certificate, with an honest
checksum (the attacker edits it in the same release) and with the right
address in the browser bar. For a wallet this is not an abstraction: a
substituted build reads the private keys of everyone who updated. A signature
made with a private key is the only thing that survives a server takeover,
which is precisely why apt verifies signatures rather than checksums from a
mirror.

⚠️ THERE IS NO KEY YET, and because of that the updater REFUSES TO INSTALL
updates — it only reports that a new version is out and offers to download it
by hand. This is not an unfinished corner but a deliberate refusal: an update
without signature verification is more dangerous than no updates at all. To
enable installation, the project owner does this once:

    python -m b_hydra.release keygen --out ~/.bhydra-release.key
    # the private key goes OFFLINE; it must NEVER be put in the repository,
    # the printed public key goes here, into RELEASE_PUBLIC_KEY

and for every release:

    python -m b_hydra.release sign --key ~/.bhydra-release.key \\
        --version v0.1.1 B-hydra-Core-windows.exe B-hydra-Core-macos ...
    # attach the resulting bhydra-release.json and .sig to the GitHub release

⚠️ Signing inside GitHub Actions is NOT allowed, not even through secrets: a
key in CI protects against exactly what TLS already protects against, and
does not protect against an account takeover — that is, against the one
threat the signature exists for. A person signs on their own machine.
"""

__all__ = ["RELEASE_PUBLIC_KEY", "have_key"]

#: PEM holding the RSA public key (SPKI). None = no key set up, update
#: installation is off. Pasted in by hand from `release keygen` output.
RELEASE_PUBLIC_KEY = None


def have_key() -> bool:
    """Whether the release key is set up. Without it `update` can only check."""
    return bool(RELEASE_PUBLIC_KEY and RELEASE_PUBLIC_KEY.strip())
