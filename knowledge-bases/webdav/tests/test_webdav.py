# Copyright Thales 2026
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Reading a share: the walk, the awkward answers, and what is refused."""

from __future__ import annotations

import ssl

import httpx
import pytest

from fred_samples_webdav_kb.source import NotAWebDavCollection, SourceUnavailable
from fred_samples_webdav_kb.webdav import (
    CA_FILE_ENV,
    MAX_DEPTH,
    AddressError,
    FileTooLarge,
    TrustStoreError,
    address,
    configured_ca_file,
    tls_policy,
)
from tests.conftest import File, Share, source_for


async def test_walks_nested_collections_and_names_paths_from_the_root():
    share = Share(
        files={
            "readme.md": File(b"top"),
            "docs/guide.md": File(b"guide"),
            "docs/deep/notes.md": File(b"notes"),
        }
    )
    source = source_for(share)
    try:
        inventory = await source.inventory()
    finally:
        await source.aclose()

    assert inventory.exhaustive
    assert sorted(entry.path for entry in inventory.files) == [
        "docs/deep/notes.md",
        "docs/guide.md",
        "readme.md",
    ]


async def test_a_collection_is_asked_for_with_its_trailing_slash():
    """Without one every server answers a redirect, which this client refuses."""
    share = Share(files={"docs/guide.md": File(b"guide")})
    source = source_for(share)
    try:
        await source.inventory()
    finally:
        await source.aclose()

    assert share.propfind_calls == ["/dav/", "/dav/docs/"]


async def test_an_entity_tag_is_the_version_when_the_share_keeps_one():
    share = Share(files={"a.md": File(b"a", etag='"abc"')})
    source = source_for(share)
    try:
        inventory = await source.inventory()
    finally:
        await source.aclose()

    assert inventory.files[0].version == 'etag:"abc"'


async def test_a_share_without_entity_tags_falls_back_to_date_and_size():
    """The 404 propstat for that one property must not be read as a value."""
    share = Share(files={"a.md": File(b"abc")}, etags=False)
    source = source_for(share)
    try:
        inventory = await source.inventory()
    finally:
        await source.aclose()

    assert inventory.files[0].version == "mtime:Mon, 01 Sep 2026 10:00:00 GMT:3"


async def test_an_entry_pointing_outside_the_share_is_ignored():
    """The href decides the key; a server must not get to choose someone else's."""
    share = Share(
        files={"a.md": File(b"a")},
        extra_hrefs={
            "/dav/": [
                "/etc/passwd",
                "/dav/../../secret.md",
                "/dav/%2e%2e/%2e%2e/secret.md",
                "https://elsewhere.example.com/dav/b.md",
            ]
        },
    )
    source = source_for(share)
    try:
        inventory = await source.inventory()
    finally:
        await source.aclose()

    assert [entry.path for entry in inventory.files] == ["a.md"]


async def test_a_share_that_never_ends_is_bounded_and_says_it_is_incomplete():
    """A symlink back to its own folder makes every level a brand-new path.

    Nothing repeats, so remembering what has been visited never ends this —
    only the depth bound does, and the run must then refuse to treat anything
    it did not reach as deleted.
    """
    share = Share(files={"a.md": File(b"a")}, endless=True)
    source = source_for(share)
    try:
        inventory = await source.inventory()
    finally:
        await source.aclose()

    assert not inventory.exhaustive
    assert len(share.propfind_calls) <= MAX_DEPTH + 1


async def test_a_child_listed_twice_is_walked_once():
    share = Share(files={"docs/a.md": File(b"a")}, repeat_children=True)
    source = source_for(share)
    try:
        inventory = await source.inventory()
    finally:
        await source.aclose()

    assert share.propfind_calls.count("/dav/docs/") == 1
    assert [entry.path for entry in inventory.files] == ["docs/a.md"]


async def test_a_redirect_is_refused_rather_than_followed():
    """Following one would present the share's credentials to whatever answered."""
    share = Share(files={"a.md": File(b"a")}, propfind_status=302)
    source = source_for(share)
    try:
        with pytest.raises(SourceUnavailable, match="redirected"):
            await source.inventory()
    finally:
        await source.aclose()


async def test_propfind_not_allowed_says_webdav_is_probably_off():
    share = Share(files={"a.md": File(b"a")}, propfind_status=405)
    source = source_for(share)
    try:
        with pytest.raises(NotAWebDavCollection, match="WebDAV"):
            await source.inventory()
    finally:
        await source.aclose()


async def test_a_refusal_names_the_credentials_and_the_method():
    share = Share(files={"a.md": File(b"a")}, propfind_status=401)
    source = source_for(share)
    try:
        with pytest.raises(SourceUnavailable, match="user name and password"):
            await source.inventory()
    finally:
        await source.aclose()


async def test_a_busy_share_is_retried():
    share = Share(files={"a.md": File(b"a")}, retry_once=True)
    source = source_for(share)
    try:
        inventory = await source.inventory()
    finally:
        await source.aclose()

    assert [entry.path for entry in inventory.files] == ["a.md"]


async def test_a_share_that_understates_a_size_is_still_bounded():
    """The listing's size is a claim; the bound is enforced on the bytes."""
    share = Share(files={"big.md": File(b"small")}, oversized={"big.md"})
    source = source_for(share, max_file_bytes=1_000)
    try:
        inventory = await source.inventory()
        with pytest.raises(FileTooLarge):
            await source.read(inventory.files[0])
    finally:
        await source.aclose()


async def test_reading_a_file_returns_its_bytes():
    share = Share(files={"docs/a.md": File(b"hello")})
    source = source_for(share)
    try:
        inventory = await source.inventory()
        assert await source.read(inventory.files[0]) == b"hello"
    finally:
        await source.aclose()


async def test_a_name_needing_escaping_survives_the_round_trip():
    share = Share(files={"a b/c&d.md": File(b"odd")})
    source = source_for(share)
    try:
        inventory = await source.inventory()
        assert [entry.path for entry in inventory.files] == ["a b/c&d.md"]
        assert await source.read(inventory.files[0]) == b"odd"
    finally:
        await source.aclose()


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "ftp://host/share/",
        "https://",
        "https://user:pass@host/share/",  # pragma: allowlist secret
        "https://host/share/?q=1",
    ],
)
def test_an_address_that_cannot_be_one_is_refused(raw: str):
    with pytest.raises(AddressError):
        address(raw)


def test_a_missing_trailing_slash_is_added_rather_than_refused():
    where = address("https://host/share")
    assert where.base_path == "/share/"
    assert where.url == "https://host/share/"


def test_a_path_is_resolved_under_the_share_and_escaped():
    where = address("https://host/share/")
    assert where.resolve("a b/c.md") == "https://host/share/a%20b/c.md"


async def test_a_folder_whose_name_needs_escaping_is_still_read():
    """The bug this guards is silent and total.

    An address is pasted encoded — `/dav/Documents%20partag%C3%A9s/` — while
    every href comes back to be compared decoded. Comparing the two forms
    matches nothing, so every entry is dropped, the inventory comes back empty
    and exhaustive, and the run retracts the entire library.
    """
    share = Share(
        files={"a.md": File(b"a"), "sous dossier/b.md": File(b"b")},
        base_path="/dav/Documents partagés/",
    )
    source = source_for(
        share, url="https://share.example.com/dav/Documents%20partag%C3%A9s/"
    )
    try:
        inventory = await source.inventory()
        assert sorted(entry.path for entry in inventory.files) == [
            "a.md",
            "sous dossier/b.md",
        ]
        assert inventory.exhaustive
        assert await source.read(inventory.files[0]) is not None
    finally:
        await source.aclose()


async def test_a_share_full_of_files_we_do_not_want_is_still_walked_whole():
    """The bound counts documents, so it must not end the walk on other files."""
    share = Share(
        files={
            **{f"pictures/{index}.png": File(b"x") for index in range(30)},
            "readme.md": File(b"a"),
        }
    )
    source = source_for(share)
    try:
        inventory = await source.inventory()
    finally:
        await source.aclose()

    assert inventory.exhaustive
    assert len(inventory.files) == 31


async def test_a_file_listed_twice_is_one_file():
    share = Share(files={"a.md": File(b"a")}, repeat_children=True)
    source = source_for(share)
    try:
        inventory = await source.inventory()
    finally:
        await source.aclose()

    assert [entry.path for entry in inventory.files] == ["a.md"]


async def test_a_plain_page_answered_to_propfind_says_this_is_not_webdav():
    """A proxy or a captive portal answers 200 with HTML, not a multistatus."""
    share = Share(files={"a.md": File(b"a")}, propfind_status=200)
    source = source_for(share)
    try:
        with pytest.raises(NotAWebDavCollection, match="not a WebDAV collection"):
            await source.inventory()
    finally:
        await source.aclose()


# ── the trust store ───────────────────────────────────────────────────────────


def test_no_private_authority_leaves_httpx_to_its_own_strict_default():
    assert tls_policy(trust_any_certificate=False) is True


def test_a_private_authority_is_added_to_what_is_already_trusted():
    """Never substituted for it.

    $SSL_CERT_FILE, the variable everyone reaches for first, replaces the whole
    trust store: one corporate root in it and this pod stops trusting every
    other authority — including whichever its own Keycloak, Control Plane and
    Knowledge Flow present. Those then fail with a handshake error that says
    nothing about the cause.
    """
    import certifi

    default = httpx.create_ssl_context()
    policy = tls_policy(trust_any_certificate=False, ca_file=certifi.where())

    assert isinstance(policy, ssl.SSLContext)
    assert len(policy.get_ca_certs()) >= len(default.get_ca_certs())
    assert policy.verify_mode is ssl.CERT_REQUIRED


def test_an_authority_that_did_not_mount_is_named_as_such():
    """In a cluster this means one thing: the volume is not there."""
    with pytest.raises(TrustStoreError, match="could not be read"):
        tls_policy(trust_any_certificate=False, ca_file="/not/mounted/ca.pem")


def test_a_file_that_is_not_a_certificate_says_so_rather_than_blaming_the_mount(
    tmp_path,
):
    """A file that is there and wrong is a different mistake from an absent one."""
    not_a_ca = tmp_path / "ca.pem"
    not_a_ca.write_text("this is not a certificate", encoding="utf-8")

    with pytest.raises(TrustStoreError, match="not a PEM certificate authority"):
        tls_policy(trust_any_certificate=False, ca_file=str(not_a_ca))


def test_accepting_any_certificate_verifies_nothing():
    policy = tls_policy(trust_any_certificate=True)

    assert isinstance(policy, ssl.SSLContext)
    assert policy.verify_mode is ssl.CERT_NONE
    assert policy.check_hostname is False


def test_the_authority_is_read_from_the_pod_s_environment(monkeypatch):
    """A path inside the pod, so never a field somebody fills in on a form."""
    monkeypatch.setenv(CA_FILE_ENV, "  /etc/ssl/private-ca/root.pem  ")
    assert configured_ca_file() == "/etc/ssl/private-ca/root.pem"

    monkeypatch.delenv(CA_FILE_ENV, raising=False)
    assert configured_ca_file() == ""
