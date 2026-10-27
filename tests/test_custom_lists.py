# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Verify Customizable Default Lists precedence, editing and routing evidence.
# Temporary personal directories and synthetic archives isolate all persisted data.
# Real writer leases, SQLite stores and ingestion exercise the production boundary.
# Stale revisions, invalid patterns and scope errors must leave accepted values intact.
# Original message bytes and manual identity decisions survive derived processing.
"""Requirements: CUSTOMIZABLE_DEFAULT_LISTS.md; no source or personal-data mutation."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from yaml import safe_dump

from mailarchiver.__main__ import IngestRequest, run_ingest
from mailarchiver.custom_lists import HISTORY_PREFIX, ListState, revision
from mailarchiver.gui_default_lists import DefaultListsApi
from mailarchiver.gui_app import IdentityPickerApi
from mailarchiver.gui_processing import PickerPage
from mailarchiver.gui_service import search_page
from mailarchiver.list_catalog import OWNERS, REPLY_ADDRESSES, registered_lists
from mailarchiver.message import MessagePatternConfig
from mailarchiver.owner_rules import OwnerRules
from mailarchiver.processing.store import connect
from mailarchiver.reply_addresses import ReplyAddressRule, ReplyAddressRules, RoutingCandidate, RoutingMatch, match_reply_address
from mailarchiver.writer_lock import WriterLease


def test_resolution_pinning_reset_and_conflicts(tmp_path: Path) -> None:
    """Personal edits seed new instances; pinned archives and their history remain stable."""
    personal=tmp_path/'preferences'
    archive=tmp_path/'archive'
    archive.mkdir()
    initial=OWNERS.state('personal',directory=personal)
    assert initial.needs_customization and not personal.exists()
    OWNERS.save_personal('include: [alice@example.test]\nexclude: []',initial.revision,personal)
    assert OWNERS.read(archive,personal).include==['alice@example.test']
    assert not (archive/'processing.sqlite3').exists()
    with closing(connect(archive,create=True)) as database, database:
        OWNERS.snapshot(database,archive,personal)
    first=OWNERS.state('personal',directory=personal)
    OWNERS.save_personal('include: [bob@example.test]',first.revision,personal)
    assert OWNERS.read(archive,personal).include==['alice@example.test']
    with pytest.raises(ValueError,match='changed'):
        OWNERS.save_personal('include: [stale]',first.revision,personal)
    with WriterLease.acquire(archive,str(archive),'test','edit','test') as lease:
        state=OWNERS.state('archive',archive,personal)
        changed=OWNERS.save_archive(archive,'',state.revision,lease,personal,reset=True)
        assert 'bob@example.test' in changed.text
        with pytest.raises(ValueError,match='changed'):
            OWNERS.save_archive(archive,'include: [stale]',state.revision,lease,personal)
    assert 'bob@example.test' in (archive/'config.yaml').read_text()
    with closing(connect(archive)) as database:
        assert database.execute('SELECT count(*) FROM processing_settings WHERE name LIKE ?',
            (HISTORY_PREFIX+'owner-rules:%',)).fetchone()==(2,)
    current=OWNERS.state('personal',directory=personal)
    assert OWNERS.save_personal('',current.revision,personal,reset=True).needs_customization
    assert OWNERS.read(archive,personal).include==['bob@example.test']


def test_all_templates_and_strict_validation(tmp_path: Path) -> None:
    for definition in registered_lists():
        state=definition.state('personal',directory=tmp_path)
        assert revision(definition.decode(state.text))==state.revision
    state=REPLY_ADDRESSES.state('personal',directory=tmp_path)
    with pytest.raises(ValueError):
        REPLY_ADDRESSES.save_personal('rules: [{id: invalid, domains: ["*.com"], family: x}]',state.revision,tmp_path)
    assert not REPLY_ADDRESSES.personal_path(tmp_path).exists()
    with pytest.raises(ValueError,match='date pattern'):
        MessagePatternConfig(mode='replace',quoted_body_date=['('])
    with pytest.raises(ValueError,match='capture'):
        ReplyAddressRule(id='capture',domains=('example.test',),family='x',name_source='capture',name_group='missing')
    with pytest.raises(ValueError,match='Family fields'):
        ReplyAddressRule(id='field',domains=('example.test',),family='{local.__class__}')


def test_name_capture_domain_header_and_observed_endpoint() -> None:
    rule=ReplyAddressRule(id='crm',domains=('CRM.EXAMPLE.TEST.',),address_pattern=r'(?P<name>[a-z._]+)\+[0-9a-f]+@crm\.example\.test',
        family='crm:{domain}',name_source='capture',name_group='name',headers=('reply-to',))
    policy=ReplyAddressRules(rules=(rule,))
    candidate=RoutingCandidate(address='Alice.Smith+ABC123@crm.example.test',header='reply-to')
    match=match_reply_address(candidate,policy)
    assert match and match.name=='Alice Smith' and match.address==candidate.address
    assert match.family=='crm:crm.example.test' and match.policy_revision==revision(policy)
    assert match_reply_address(candidate.model_copy(update={'header':'from'}),policy) is None
    assert match_reply_address(candidate.model_copy(update={'address':'Alice.Smith+ABC123@evil-crm.example.test'}),policy) is None


def test_bridge_archive_legacy_owner_and_unknown_scope(tmp_path: Path) -> None:
    archive=tmp_path/'archive'
    archive.mkdir()
    (archive/'owner-names.txt').write_text('legacy@example.test\n')
    api=DefaultListsApi(archive,directory=tmp_path/'personal')
    state=ListState.model_validate(api.load({'key':'owner-rules','scope':'archive'}))
    assert 'legacy@example.test' in state.text
    changed=api.update({'key':state.key,'scope':state.scope,'text':'include: [new@example.test]', 'revision':state.revision})
    assert 'new@example.test' in ListState.model_validate(changed).text
    assert (archive/'owner-names.txt').read_text()=='legacy@example.test\n'
    with pytest.raises(ValueError,match='personal scope'):
        api.load({'key':'display-policy','scope':'archive'})
    with pytest.raises(ValueError,match='Unknown'):
        api.load({'key':'../../escape','scope':'personal'})


def test_real_ingest_records_routing_evidence_without_alias_merges(tmp_path: Path) -> None:
    archive=tmp_path/'archive'
    archive.mkdir()
    policy=ReplyAddressRules(rules=(ReplyAddressRule(id='crm',domains=('crm.example.test',),family='crm',name_source='display-name'),))
    with WriterLease.acquire(archive,str(archive),'test','rules','test') as lease:
        REPLY_ADDRESSES.save_archive(archive,safe_dump(policy.model_dump(mode='json')),revision(REPLY_ADDRESSES.read(archive)),lease)
    raw=(b'From: Alice <Alice+ABC@crm.example.test>\nTo: Bob <bob+123@crm.example.test>\n'
         b'Cc: Carol <carol+456@crm.example.test>\nBcc: Dan <dan+789@crm.example.test>\n'
         b'Reply-To: Erin <Erin+DEF@crm.example.test>\nDate: Wed, 21 Oct 2026 10:00:00 -0400\nSubject: Routing fixture\n\noriginal body\n')
    source=tmp_path/'input.eml'
    source.write_bytes(raw)
    run_ingest(IngestRequest(archive=archive,roots=[str(source)],scan_policy='not-scanned',
        owner_rules=OwnerRules(include=['owner@example.test'])),terminal=False)
    with closing(connect(archive)) as database:
        matches=[RoutingMatch.model_validate_json(row[0]) for row in database.execute("SELECT value FROM evidence WHERE kind='routing-address'")]
        assert {match.header for match in matches}=={'from','to','cc','bcc','reply-to'}
        assert next(match for match in matches if match.header=='from').address=='Alice+ABC@crm.example.test'
        assert database.execute('SELECT count(*) FROM persons').fetchone()[0]>=4
        with database:
            database.execute("UPDATE persons SET canonical_name='Manual Alice',manual=1 WHERE canonical_name='Alice'")
    # Same input with explicit reprocessing exercises persisted policy and manual-name protection.
    run_ingest(IngestRequest(archive=archive,roots=[str(source)],scan_policy='not-scanned',reprocess=True,
        owner_rules=OwnerRules(include=['owner@example.test'])),terminal=False)
    with closing(sqlite3.connect(archive/'processing.sqlite3')) as database:
        assert database.execute("SELECT count(*) FROM persons WHERE canonical_name='Manual Alice' AND manual=1").fetchone()==(1,)
    assert source.read_bytes()==raw
    assert any(raw in path.read_bytes() for path in archive.rglob('*.mbox'))


def test_provider_add_remove_rebuilds_automatic_affiliations(tmp_path: Path) -> None:
    """Explicit replay applies provider edits, preserving durable manual institution data."""
    from mailarchiver.list_catalog import PROVIDER_DOMAINS
    from mailarchiver.list_models import ProviderDomains
    archive=tmp_path/'archive'
    archive.mkdir()
    source=tmp_path/'input.eml'
    source.write_bytes(b'From: Alice <alice@example.test>\nTo: Bob <bob@example.test>\n'
        b'Date: Wed, 21 Oct 2026 10:00:00 -0400\nSubject: Provider fixture\n\nbody\n')
    def policy(domains: tuple[str,...]) -> None:
        with WriterLease.acquire(archive,str(archive),'test','provider','test') as lease:
            PROVIDER_DOMAINS.save_archive(archive,safe_dump(ProviderDomains(domains=domains).model_dump(mode='json')),
                revision(PROVIDER_DOMAINS.read(archive)),lease)
    def ingest(*, reprocess: bool = False) -> None:
        run_ingest(IngestRequest(archive=archive,roots=[] if reprocess else [str(source)],scan_policy='not-scanned',reprocess=reprocess,
            owner_rules=OwnerRules(include=['owner@example.test'])),terminal=False)
    policy(('example.test',))
    ingest()
    with closing(connect(archive)) as database:
        assert database.execute("SELECT provider FROM organization_domains WHERE domain='example.test'").fetchone()==(1,)
        assert database.execute('SELECT count(*) FROM affiliations WHERE manual=0').fetchone()==(0,)
    policy(())
    ingest()
    with closing(connect(archive)) as database:
        assert database.execute("SELECT provider FROM organization_domains WHERE domain='example.test'").fetchone()==(1,)
        assert database.execute('SELECT count(*) FROM affiliations WHERE manual=0').fetchone()==(0,)
    ingest(reprocess=True)
    with closing(connect(archive)) as database, database:
        assert database.execute("SELECT provider FROM organization_domains WHERE domain='example.test'").fetchone()==(0,)
        assert database.execute('SELECT count(*) FROM affiliations WHERE manual=0').fetchone()[0]>=2
    assert sum(len(group.addresses) for group in PickerPage.model_validate(
        IdentityPickerApi(archive,'institution').query({})).groups)==2
    policy(('example.test',))
    ingest(reprocess=True)
    assert not any(group.addresses for group in PickerPage.model_validate(
        IdentityPickerApi(archive,'institution').query({})).groups)
    policy(())
    ingest(reprocess=True)
    with closing(connect(archive)) as database, database:
        database.execute("UPDATE organizations SET name='Manual institution',manual=1 WHERE name='example.test'")
    assert len(search_page(archive,'any:"Manual institution"').results)==1
    policy(('example.test',))
    ingest(reprocess=True)
    with closing(connect(archive)) as database:
        assert database.execute('SELECT count(*) FROM affiliations WHERE manual=0').fetchone()==(0,)
        assert database.execute("SELECT count(*) FROM organizations WHERE name='Manual institution' AND manual=1").fetchone()==(1,)
    assert not any(group.addresses for group in PickerPage.model_validate(
        IdentityPickerApi(archive,'institution').query({})).groups)
    assert not search_page(archive,'any:"Manual institution"').results
    policy(())
    ingest(reprocess=True)
    with closing(connect(archive)) as database:
        assert database.execute("SELECT count(*) FROM organizations WHERE name='Manual institution' AND manual=1").fetchone()==(1,)
        assert database.execute('SELECT count(*) FROM affiliations WHERE manual=0').fetchone()[0]>=2
    restored=next(group for group in PickerPage.model_validate(
        IdentityPickerApi(archive,'institution').query({})).groups if group.label=='Manual institution')
    assert len(restored.addresses)==2
    assert len(search_page(archive,'any:"Manual institution"').results)==1


def test_legacy_contact_extension_and_personal_owner_exclusions(tmp_path: Path) -> None:
    """Legacy extension files still resolve, and owner defaults keep their exclusions."""
    from mailarchiver.document_options import DocumentOptions
    from mailarchiver.list_catalog import CONTACT_FILTERS
    archive=tmp_path/'archive'
    archive.mkdir()
    personal=tmp_path/'personal'
    (archive/'contact_filters.yaml').write_text("version: 1\nmode: extend\nservice:\n  domain_patterns: ['^crm.example.test$']\n")
    resolved=CONTACT_FILTERS.read(archive,personal)
    assert '^crm.example.test$' in resolved.service.domain_patterns
    assert len(resolved.service.domain_patterns)>1
    with WriterLease.acquire(archive,str(archive),'test','contact','test') as lease:
        CONTACT_FILTERS.save_archive(archive,safe_dump(resolved.model_dump(mode='json')),revision(resolved),lease,personal)
    assert 'mode: replace' in (archive/'contact_filters.yaml').read_text()
    # Snapshot a personal template in an archive with no legacy/config owner entry.
    state=OWNERS.state('personal',directory=personal)
    OWNERS.save_personal("include: ['alice@*']\nexclude: [alice@lists.example.test]",state.revision,personal)
    with closing(connect(archive)) as database, database:
        OWNERS.snapshot(database,archive,personal)
    rules=DocumentOptions(archive).defaults()
    assert rules.matches('alice@example.test') and not rules.matches('alice@lists.example.test')


def test_pipeline_without_reply_python_source(tmp_path: Path) -> None:
    """Frozen installs lack helper .py files; real ingestion must use importable bytecode."""
    import os
    import py_compile
    import shutil
    import subprocess
    import sys
    from mailarchiver import reply_addresses
    package=tmp_path/'modules'/'mailarchiver'
    shutil.copytree(Path(reply_addresses.__file__).parent,package,ignore=shutil.ignore_patterns('__pycache__'))
    helper=package/'reply_addresses.py'
    py_compile.compile(str(helper),cfile=str(helper.with_suffix('.pyc')),doraise=True)
    helper.unlink()
    source=tmp_path/'input.eml'
    source.write_bytes(b'From: Alice <alice@example.test>\nDate: Wed, 21 Oct 2026 10:00:00 -0400\nSubject: Bytecode fixture\n\nbody\n')
    program=('import sys; from pathlib import Path; from mailarchiver import reply_addresses; '
        'assert not Path(reply_addresses.__file__).with_suffix(".py").exists(); '
        'from mailarchiver.__main__ import IngestRequest,run_ingest; from mailarchiver.owner_rules import OwnerRules; '
        'run_ingest(IngestRequest(archive=Path(sys.argv[1]),roots=[sys.argv[2]],scan_policy="not-scanned",'
        'owner_rules=OwnerRules(include=["owner@example.test"])),terminal=False)')
    result=subprocess.run([sys.executable,'-c',program,str(tmp_path/'archive'),str(source)],
        env=os.environ|{'PYTHONPATH':str(package.parent)},cwd=tmp_path,text=True,capture_output=True,check=False)
    assert result.returncode==0,result.stdout+result.stderr
    with closing(connect(tmp_path/'archive')) as database:
        assert database.execute('SELECT count(*) FROM message_state WHERE catalog_message_pk IS NOT NULL').fetchone()==(1,)
