"""numeric_v2 실행 경로. 학습이나 legacy loader를 호출하지 않는다."""
from __future__ import annotations
import errno
import hashlib
import json
import os
import re
import shutil
from pathlib import Path


def _real_source(cfg, section):
    """Return a validated real_hotspot_v1 manifest, or None for numeric_v2."""
    from simcompare.adapters.real_hotspot import CONTRACT_VERSION, load_real_manifest
    from simcompare.numeric_schema import NumericContractError
    from simcompare.numeric_images import resolve_path
    if not isinstance(section, dict) or not isinstance(section.get('data_dir'), str):
        raise NumericContractError('numeric_input_required')
    root = _numeric_path(cfg, section['data_dir'])
    manifest_path = section.get('manifest_path', 'manifest.json')
    candidate = resolve_path(root, manifest_path)
    try:
        header = json.loads(candidate.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if header.get('schema_version') != CONTRACT_VERSION:
        return None
    if cfg.get('mode') != 'real':
        raise NumericContractError('real_source_in_synthetic_mode')
    return root, load_real_manifest(root, manifest_path)


def _configured_real(cfg, section, real, detail=None):
    from simcompare.adapters.real_hotspot import configured_dataset
    root, manifest = real
    assignment = section.get('condition_assignment') if isinstance(section, dict) else None
    if assignment is None:
        return None
    # detail is the compare bucket detail: {} = full (admission charges every group's output), else summary
    dataset = configured_dataset(root, manifest, assignment,
                                 **({} if detail is None else {'top_buckets': detail.get('top_buckets')}))
    return root, dataset


def _write_inspection(output, prefix, payload, rows):
    import csv
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / f'{prefix}.json'
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    csv_path = output / f'{prefix}.csv'
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with csv_path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields or ['status'])
        writer.writeheader()
        writer.writerows(rows or [{'status': 'no_records'}])
    return [str(json_path), str(csv_path)]


def _dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str)


def _write_csv(path, rows):
    import csv
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields or ['status'])
        writer.writeheader()
        writer.writerows(rows or [{'status': 'no_records'}])


def _real_area_rows(side, groups):
    rows = []
    for group in groups:
        row = {'side': side, 'group': group.key.__dict__, 'status': group.union.status,
               'union_um2': group.union.value, 'partial_union_um2': group.union.partial_value,
               'line_um2': group.line.area.value, 'pixel_effective_um2': group.pixel.area.value,
               'transfer_um2': group.transfer.value, 'unmapped_pair_count': group.coverage.unmapped,
               'missing_count': group.coverage.missing, 'is_synthetic': False}
        for source in group.source_cases:
            raw_records = source.raw.get('records', []) if isinstance(source.raw, dict) else []
            raw_record = next((r for r in raw_records if r.get('diameter_um') == source.diameter_um), {})
            csv_area = raw_record.get('csv_area') or {}
            rows.append({**row, 'case_id': source.identity.case_id, 'diameter_um': source.diameter_um,
                         'case_area_um2': source.measurement.ca_um2,
                         'case_partial_area_um2': source.measurement.partial_value,
                         'input_status': source.input_status,
                         'csv_status': raw_record.get('csv_status'),
                         'csv_area_um2': csv_area.get('value'),
                         'csv_raw_value': csv_area.get('raw_area'),
                         'area_difference_um2': raw_record.get('area_difference_um2'),
                         'area_tolerance_um2': raw_record.get('area_tolerance_um2'),
                         'reasons': list(source.measurement.reasons)})
    return rows


class _ComparisonStream:
    """Streams real_hotspot_comparison_v2 into sibling temp files; nothing is published before commit().

    Per side: AreaResult header, groups one by one (group fields, then each source_case separately) and
    `cases_raw` once. The full v1 dict (or asdict of raw) is never built. On any exception, including
    KeyboardInterrupt, abort() removes the temp files; until the publication starts (the journal is written) an
    existing comparison.json/csv stays untouched. During the publication the pair can be torn for a moment and an
    interrupted run is repaired by recover (forward after the commit marker, otherwise back). Recovery fails closed:
    state it cannot read is reported as `comparison_recovery_unreadable` and left exactly as found.
    """
    SCHEMA = 'real_hotspot_comparison_v2'

    def __init__(self, output, top_buckets=None, masks=False):
        """top_buckets=None writes full bucket lists; an int marks the file as bucket_detail "summary".

        masks=True also publishes comparison_masks.zip (compare_masks.py) in the same transaction."""
        self.output = Path(output)
        self.top_buckets = top_buckets
        self._with_masks = masks
        self._fh = None
        self._masks = self._masks_fh = self.masks_tmp = None
        self._masks_ready = False
        self._masks_dropped = False
        self._temps = []

    def _open_temp(self, name):
        import secrets
        path = self.output / f'.{name}.{os.getpid()}.{secrets.token_hex(4)}.tmp'
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        self._temps.append(path)
        return path, fd

    _STALE_TEMP = re.compile(r'\.(?:comparison\.json|comparison\.csv|comparison_masks\.zip)\.([0-9]+)\.[0-9a-f]{8}\.tmp')

    def _sweep_stale_temps(self):
        """Temp files of earlier runs that died (a killed compare leaves a 50-72 MB comparison_masks.zip temp at ETX scale).
        Only names this class creates, only when their owning process is surely gone: a live run's temps stay."""
        from simcompare.publication import owner_is_gone
        for name in os.listdir(str(self.output)):
            found = self._STALE_TEMP.fullmatch(name)
            if found and owner_is_gone(int(found.group(1))):
                try:
                    self._unlink(self.output / name)
                except OSError:
                    pass

    def begin(self):
        self.output.mkdir(parents=True, exist_ok=True)
        self._recover()  # a previous run killed during publication: roll forward if committed, else back
        if self._present(self._marker()):
            raise OSError(errno.EIO, 'cannot finish the committed publication in ' + str(self.output))
        self._sweep_stale_temps()
        self.json_tmp, fd = self._open_temp('comparison.json')
        self._fh = os.fdopen(fd, 'w', encoding='utf-8', newline='', buffering=1 << 20)
        if self._with_masks:
            from simcompare.compare_masks import ARTEFACT_NAME, MasksWriter
            self.masks_tmp, fd = self._open_temp(ARTEFACT_NAME)
            self._masks_fh = os.fdopen(fd, 'wb', buffering=1 << 20)
            self._masks = MasksWriter(self._masks_fh)
        self._fh.write('{"schema_version":"%s","source_contract_version":"real_hotspot_v1","is_synthetic":false'
                       % self.SCHEMA)
        if self.top_buckets is not None:  # absent bucket_detail means full (T2 layout, byte-identical)
            self._fh.write(',"bucket_detail":"summary","top_buckets":%d' % self.top_buckets)

    def side(self, name, result):
        write = self._fh.write
        if self._masks is not None:
            try:
                self._masks.add_side(name, result)
            except (TypeError, ValueError, KeyError, AttributeError) as exc:   # the artefact is optional: never cost the compare
                self._drop_masks(exc)
        if result is None:
            write(',"%s":null' % name)
            return
        import dataclasses
        header = dataclasses.replace(result, groups={}).to_dict()
        header.pop('groups')
        write(',"%s":%s,"groups":[' % (name, _dumps(header)[:-1]))
        cases_raw, ids = {}, {}
        for number, key in enumerate(sorted(result.groups)):
            group = result.groups[key]
            head = dataclasses.replace(group, source_cases=()).to_dict()
            head.pop('source_cases')
            write(('' if number == 0 else ',') + _dumps(head)[:-1] + ',"source_cases":[')
            for index, source in enumerate(group.source_cases):
                ref = ids.get(id(source.raw))
                if ref is None:
                    base = ref = source.identity.case_id
                    suffix = 1
                    while ref in cases_raw and cases_raw[ref] != source.raw:
                        suffix += 1
                        ref = '%s#%d' % (base, suffix)
                    cases_raw[ref] = source.raw
                    ids[id(source.raw)] = ref
                piece = dataclasses.asdict(dataclasses.replace(source, raw=None))
                del piece['raw']
                piece['raw_ref'] = ref
                write(('' if index == 0 else ',') + _dumps(piece))
            write(']}')
        write('],"cases_raw":{')
        for index, (ref, raw) in enumerate(cases_raw.items()):
            write(('' if index == 0 else ',') + _dumps(ref) + ':' + _dumps(raw))
        write('}}')

    def commit(self, tail, rows):
        write = self._fh.write
        write(',"records":[')
        for index, row in enumerate(rows):
            write(('' if index == 0 else ',') + _dumps(row))
        write(']')
        for key, value in tail.items():
            write(',%s:%s' % (_dumps(key), _dumps(value)))
        write('}')
        self._finish(self._fh)
        self._fh = None
        csv_tmp, fd = self._open_temp('comparison.csv')
        os.close(fd)
        _write_csv(csv_tmp, rows)
        with csv_tmp.open('rb') as handle:
            os.fsync(handle.fileno())
        if self._masks is not None:
            try:
                self._masks.close()
            except (TypeError, ValueError, KeyError, AttributeError) as exc:
                self._drop_masks(exc)
            else:
                self._finish(self._masks_fh)
                self._masks = self._masks_fh = None
                self._masks_ready = True
        self._publish(csv_tmp)
        self._sync_dir()

    # -- all-or-nothing publication of the json/csv pair ----------------------------------------------
    # Protocol (every step is a separate filesystem operation; the commit decision is one atomic rename):
    #   1. write the journal (which of the two files exist) atomically (partial file, then rename)
    #                                                             -- no journal yet: nothing was replaced
    #   2. hard-link (copy as a fallback) the previous pair to *.prev
    #   3. os.replace json, os.replace csv                        -- recover (no marker) = restore *.prev
    #   4. COMMIT: atomically rename .comparison.committed.partial -> .comparison.committed
    #   5. delete *.prev, then the journal, then the marker       -- recover (marker) = finish this deletion
    # The rollback material (*.prev, journal) is only destroyed after the marker exists, and recovery only acts on
    # a journal/marker it read successfully (an unreadable one raises and changes nothing), so after a recovery
    # that completed the pair on disk is (OLD_JSON, OLD_CSV) or (NEW_JSON, NEW_CSV). Between a process death and
    # the next begin() (or a recovery that reported comparison_recovery_unreadable) the two files can be of
    # different generations; the leftover .comparison.publishing / .comparison.committed show that. A failure or
    # interrupt in step 5 never rolls back.
    # comparison_masks.zip (compare_masks.py) joins the transaction when the stream was created with masks=True; recovery
    # always knows all three names so a journal left by either kind of run is repaired by any later run
    _NAMES = ('comparison.json', 'comparison.csv', 'comparison_masks.zip')
    _REQUIRED = _NAMES[:2]

    def _journal(self):
        return self.output / '.comparison.publishing'

    def _prev(self, name):
        return self.output / f'.{name}.prev'

    def _marker(self):
        return self.output / '.comparison.committed'

    @staticmethod
    def _create(path, text):
        with open(str(path), 'w', encoding='utf-8') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _unlink(path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def _present(path):
        """Exists or not; an error other than "not there" is raised (Path.exists() hides it on Python >= 3.12)."""
        try:
            os.lstat(str(path))
        except (FileNotFoundError, NotADirectoryError):
            return False
        return True

    @staticmethod
    def _read_text(path):
        return path.read_text(encoding='utf-8')

    def _unreadable(self, path, exc):
        from simcompare.numeric_schema import NumericContractError
        return NumericContractError(
            'comparison_recovery_unreadable',
            '%s cannot be used (%s); nothing in %s was changed. Fix the cause and run compare again, or restore '
            'comparison.json/comparison.csv from .comparison.json.prev/.comparison.csv.prev by hand and delete the '
            '.comparison.* files' % (path, exc, self.output))

    def _sync_dir(self):
        try:
            dir_fd = os.open(str(self.output), os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        except OSError:
            pass
        finally:
            os.close(dir_fd)

    def _publish(self, csv_tmp):
        if self._present(self._marker()):  # begin() could not finish an earlier commit: do not mix generations
            raise OSError(errno.EIO, 'a previous publication is committed but not cleaned up: ' + str(self._marker()))
        staged = {'comparison.json': self.json_tmp, 'comparison.csv': csv_tmp}
        if self._masks_ready:
            staged[self._NAMES[2]] = self.masks_tmp
        removed = ()
        if self._masks_dropped and self._present(self.output / self._NAMES[2]):
            removed = (self._NAMES[2],)  # the dropped artefact's predecessor must not stay beside the new pair
        names = [name for name in self._NAMES if name in staged or name in removed]
        existed = {name: self._present(self.output / name) for name in names}
        try:
            journal_tmp = self._journal().with_name(self._journal().name + '.partial')
            self._create(journal_tmp, json.dumps(existed))
            os.replace(str(journal_tmp), str(self._journal()))  # never a half-written journal
            for name in names:
                if existed[name]:
                    self._backup(self.output / name, self._prev(name))
            for name in names:
                if name in staged:
                    os.replace(str(staged[name]), str(self.output / name))
                    self._temps.remove(staged[name])
                else:
                    self._unlink(self.output / name)
            self._sync_dir()
            pending = self._marker().with_name(self._marker().name + '.partial')
            self._create(pending, 'committed')
            os.replace(str(pending), str(self._marker()))  # the commit decision
        except BaseException:
            self._recover()
            raise
        self._finish_commit()

    @staticmethod
    def _backup(source, backup):
        if _ComparisonStream._present(backup):
            backup.unlink()
        try:
            os.link(str(source), str(backup))
            return
        except OSError:
            pass
        partial = backup.with_name(backup.name + '.partial')
        shutil.copyfile(str(source), str(partial))
        with partial.open('rb') as handle:
            os.fsync(handle.fileno())
        os.replace(str(partial), str(backup))

    def _discard_material(self):
        """Delete *.prev (+ partial copies) then the journal; False (nothing more deleted) at the first error."""
        try:
            for name in self._NAMES:
                prev = self._prev(name)
                self._unlink(prev)
                self._unlink(prev.with_name(prev.name + '.partial'))
            self._unlink(self._journal().with_name(self._journal().name + '.partial'))
            self._unlink(self._journal())
        except OSError:
            return False
        return True

    def _finish_commit(self):
        """Roll forward: the marker exists, so the new pair is final; drop the rollback material, marker last."""
        if self._discard_material():
            try:
                self._unlink(self._marker().with_name(self._marker().name + '.partial'))
                self._unlink(self._marker())
            except OSError:
                pass

    def _recover(self):
        """Repair an interrupted publication: forward when the commit marker exists, otherwise back. Idempotent.

        Fails closed: if the marker or the journal cannot be read, or a backup cannot be looked up, nothing is
        replaced or deleted and NumericContractError('comparison_recovery_unreadable') is raised.
        """
        try:
            committed = self._present(self._marker())
        except OSError as exc:
            raise self._unreadable(self._marker(), exc) from exc
        if committed:
            self._finish_commit()
        else:
            self._rollback()

    def _rollback(self):
        """Restore the generation recorded in the journal; idempotent, safe to repeat after a crash."""
        journal = self._journal()
        try:
            existed = json.loads(self._read_text(journal))
        except FileNotFoundError:  # the journal is renamed into place first: no journal, nothing was replaced
            self._unlink(journal.with_name(journal.name + '.partial'))
            self._unlink(self._marker().with_name(self._marker().name + '.partial'))
            return
        except (OSError, ValueError) as exc:  # unreadable or corrupt: unknown how far the publication got
            raise self._unreadable(journal, exc) from exc
        if (not isinstance(existed, dict) or set(existed) - set(self._NAMES)
                or any(not isinstance(existed.get(name), bool) for name in self._REQUIRED)
                or any(not isinstance(value, bool) for value in existed.values())):
            raise self._unreadable(journal, 'unexpected content')
        try:
            has_prev = {name: self._present(self._prev(name)) for name in existed}
        except OSError as exc:
            raise self._unreadable(self.output, exc) from exc
        for name in existed:
            final = self.output / name
            if existed[name]:
                if has_prev[name]:
                    os.replace(str(self._prev(name)), str(final))
            else:
                self._unlink(final)
        self._unlink(self._marker().with_name(self._marker().name + '.partial'))
        self._discard_material()

    def _drop_masks(self, exc):
        """The mask artefact could not be built (not an I/O error): publish comparison.json/csv without it.
        compare-windows then recomputes compute_areas, which gives the same numbers."""
        import sys
        self._masks.abandon()
        self._masks = None
        try:
            self._masks_fh.close()
        except (OSError, ValueError):
            pass
        self._masks_fh = None
        try:
            self.masks_tmp.unlink()
        except OSError:
            pass
        if self.masks_tmp in self._temps:
            self._temps.remove(self.masks_tmp)
        self._with_masks = False
        self._masks_dropped = True
        print(f'compare: comparison_masks.zip not written ({type(exc).__name__}: {exc}); compare-windows will recompute',
              file=sys.stderr)

    @staticmethod
    def _finish(handle):
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()

    def abort(self):
        if self._masks is not None:
            self._masks.abandon()
            self._masks = None
        if self._masks_fh is not None:
            try:
                self._masks_fh.close()
            except (OSError, ValueError):
                pass
            self._masks_fh = None
        if self._fh is not None:
            try:
                self._fh.close()
            except (OSError, ValueError):
                pass
            self._fh = None
        for path in self._temps:
            try:
                path.unlink()
            except OSError:
                pass
        self._temps = []


def _real_validation(cfg, section, output):
    _, manifest = section
    rows, reasons = [], set()
    for case in manifest['cases']:
        for record in case['records']:
            status = record['input_status']
            reason = record.get('reason')
            value = record.get('png_area_um2') if status == 'observed' else None
            if reason: reasons.add(reason)
            if record.get('csv_status') not in (None, 'consistent'):
                reasons.add('csv_'+record['csv_status'])
            rows.append({'case_id': case['case_name'], 'diameter_um': record['diameter_um'],
                         'failure_type': case['failure_type'], 'input_status': status,
                         'area_um2': value, 'csv_area_um2': (record.get('csv_area') or {}).get('value'),
                         'area_difference_um2': record.get('area_difference_um2'),
                         'area_tolerance_um2': record.get('area_tolerance_um2'),
                         'classification_status': case['classification_status'], 'reason': reason})
    reasons.update(d.get('status', '') for d in manifest.get('diagnostics', []))
    payload = {'schema_version': 'real_hotspot_validation_v1', 'source_contract_version': manifest['schema_version'],
               'is_synthetic': False, 'status': 'partial', 'reasons': sorted(filter(None, reasons)),
               'raw_observations_status': 'available' if any(r['area_um2'] is not None for r in rows) else 'unavailable',
               'classified_comparison_status': 'unavailable', 'map_status': 'requires_explicit_condition_assignment',
               'physical_metadata': manifest['physical_metadata'], 'expected_case_coverage_status': manifest['expected_case_coverage_status'],
               'diagnostics': manifest['diagnostics'], 'records': rows,
               'source_payload_hash': manifest['source_payload_hash']}
    paths = _write_inspection(output, 'validation', payload, rows)
    return {'status': 'partial', 'reasons': payload['reasons'], 'outputs': paths, 'exit_code': 3}


def _compare_options(cfg):
    """Validate the optional top-level `compare` section; returns (full_bucket_detail, top_buckets)."""
    from simcompare.linepixel import DEFAULT_TOP_BUCKETS
    from simcompare.numeric_schema import NumericContractError
    section = cfg.get('compare', {})
    if section is None:
        section = {}
    if not isinstance(section, dict) or set(section) - {'full_bucket_detail', 'top_buckets'}:
        raise NumericContractError('invalid_compare_options', 'compare accepts only full_bucket_detail and top_buckets')
    full = section.get('full_bucket_detail', False)
    top = section.get('top_buckets', DEFAULT_TOP_BUCKETS)
    if type(full) is not bool:
        raise NumericContractError('invalid_compare_options', 'full_bucket_detail must be true or false')
    if type(top) is not int or top <= 0:
        raise NumericContractError('invalid_compare_options', 'top_buckets must be a positive integer')
    return full, top


def load_numeric_config(path):
    import yaml
    from simcompare.numeric_schema import NumericContractError, text
    import hashlib
    path = Path(path).resolve()
    raw = path.read_bytes()
    try:
        cfg = yaml.safe_load(raw.decode('utf-8'))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise NumericContractError('invalid_numeric_config',str(exc)) from exc
    if not isinstance(cfg,dict) or cfg.get('schema_version') != 'numeric_config_v1':
        raise NumericContractError('unsupported_numeric_config', 'numeric_config_v1 설정을 사용하세요; legacy export의 numeric 변환은 G2 pending입니다')
    allowed = {'schema_version','mode','output_dir','reference','target','map','review_rois','compare'}
    if set(cfg)-allowed or cfg.get('mode') not in ('synthetic','real') or not isinstance(cfg.get('output_dir'),str):
        raise NumericContractError('invalid_numeric_config')
    text(cfg['output_dir'],'output_dir')
    _compare_options(cfg)
    cfg['_config_dir'] = path.parent
    cfg['_config_path'] = path
    cfg['_config_sha256'] = hashlib.sha256(raw).hexdigest()
    return cfg


def _numeric_path(cfg, value):
    from simcompare.numeric_schema import text
    path = Path(text(value,'config path'))
    return path if path.is_absolute() else cfg['_config_dir']/path


def _numeric_input(cfg, section):
    from simcompare.adapters.numeric import load_dataset as load_numeric
    from simcompare.numeric_schema import NumericContractError
    if not isinstance(section,dict):
        raise NumericContractError('numeric_input_required')
    allowed = {'data_dir','manifest_path','product_id','asset_manifest','density_contracts','unit_bases','panel'}
    if set(section)-allowed:
        raise NumericContractError('unknown_numeric_input_fields')
    root = _numeric_path(cfg,section.get('data_dir'))
    dataset = load_numeric(root,manifest_path=section.get('manifest_path','manifest.json'),mode=cfg['mode'])
    products = {c.identity.product_id for c in dataset.cases}
    if len(products) != 1 or section.get('product_id') not in products:
        raise NumericContractError('single_declared_product_required')
    return root,dataset


def _numeric_rates(dataset, section):
    from simcompare.density import DensityContract, adapt_density
    from simcompare.linepixel import PanelSpec, UnitAreaBasis, compute_areas, compute_rates
    from simcompare.numeric_schema import NumericContractError
    def declarations(name):
        values=section.get(name,[])
        if not isinstance(values,list) or any(not isinstance(v,dict) for v in values):
            raise NumericContractError('invalid_numeric_config', name+'는 객체 목록이어야 합니다')
        return values
    contracts={}
    for raw in declarations('density_contracts'):
        try:contract=DensityContract(**raw)
        except TypeError as exc:raise NumericContractError('invalid_numeric_config','density_contracts 필드를 확인하세요') from exc
        if contract.distribution_id in contracts:raise NumericContractError('duplicate_density_contract')
        contracts[contract.distribution_id]=contract
    distributions={}
    for selected in sorted({r.distribution_id for r in dataset.density}):
        distribution=adapt_density([r for r in dataset.density if r.distribution_id==selected],contract=contracts.get(selected))
        if distribution.process_layer in distributions:raise NumericContractError('ambiguous_layer_density_distribution')
        distributions[distribution.process_layer]=distribution
    if set(contracts)-{r.distribution_id for r in dataset.density}:raise NumericContractError('unused_density_contract')
    bases={}
    domain=dataset.unit_domain
    for raw in declarations('unit_bases'):
        try:basis=UnitAreaBasis(**{**raw,'unit_size_um':tuple(raw['unit_size_um'])})
        except (KeyError,TypeError) as exc:raise NumericContractError('invalid_numeric_config','unit_bases의 frame/unit_size_um/evidence를 확인하세요') from exc
        key=(section['product_id'],basis.frame_id)
        if key in bases:raise NumericContractError('duplicate_unit_basis')
        declared=(domain['unit_width_um'],domain['unit_height_um'])
        measured=(domain['measured_width_um'],domain['measured_height_um'])
        if (domain['frame_id']!=basis.frame_id or declared!=tuple(basis.unit_size_um) or declared!=measured
                or domain.get('domain_shape_units_yx',[1,1])!=[1,1]):
            from dataclasses import replace
            basis=replace(basis,basis='manifest_unit_domain_mismatch')
        bases[key]=basis
    raw_panel=section.get('panel')
    if raw_panel is not None and not isinstance(raw_panel,dict):
        raise NumericContractError('invalid_numeric_config','panel은 치수와 length_unit을 가진 객체여야 합니다')
    try:panels={} if raw_panel is None else {section['product_id']:PanelSpec(**raw_panel)}
    except TypeError as exc:raise NumericContractError('invalid_numeric_config','panel 필드를 확인하세요') from exc
    return compute_rates(compute_areas(dataset),distributions,unit_bases=bases,panels=panels)


def run_numeric_compare(cfg, out=None):
    try:
        return _run_numeric_compare(cfg, out)
    except Exception as exc:
        # real mode: failures before any output is written (source validation, mixed formats, a moved import folder)
        # and inside the transactional publication keep the previous good generation; cli.py's failure handler then
        # writes only error.json / execution.json. Failures of the non-transactional writers set the flag to False
        # (reports are removed as before). Synthetic compare is unchanged.
        if cfg.get('mode') == 'real' and getattr(exc, 'keeps_previous_outputs', None) is None:
            exc.keeps_previous_outputs = True
        raise


def _run_numeric_compare(cfg, out=None):
    from simcompare.linepixel import compare_rates
    from simcompare.report import comparison_report, write_numeric_report
    reference_real = _real_source(cfg, cfg.get('reference'))
    target_real = _real_source(cfg, cfg.get('target'))
    if reference_real is not None or target_real is not None:
        if reference_real is None or target_real is None:
            from simcompare.numeric_schema import NumericContractError
            raise NumericContractError('mixed_real_and_numeric_source_formats')
        if (cfg['reference'].get('condition_assignment') is not None and
                cfg['target'].get('condition_assignment') is not None):
            from simcompare.linepixel import compute_areas
            output = Path(out) if out is not None else _numeric_path(cfg, cfg['output_dir'])
            full_detail, top = _compare_options(cfg)
            detail = {} if full_detail else {'bucket_detail': 'summary', 'top_buckets': top}
            stream = _ComparisonStream(output, None if full_detail else top, masks=True)
            try:
                reference_configured = _configured_real(cfg, cfg['reference'], reference_real, detail)
                left = compute_areas(reference_configured[1], **detail) if reference_configured[1].cases else None
                left_rows = _real_area_rows('reference', left.groups.values()) if left is not None else []
                left_unresolved = reference_configured[1].provenance.get('unresolved_same_name_cases', [])
                left_present = left is not None
                stream.begin()
                stream.side('reference', left)
                del reference_configured, left
                target_configured = _configured_real(cfg, cfg['target'], target_real, detail)
                right = compute_areas(target_configured[1], **detail) if target_configured[1].cases else None
                right_rows = _real_area_rows('target', right.groups.values()) if right is not None else []
                right_unresolved = target_configured[1].provenance.get('unresolved_same_name_cases', [])
                has_cases = left_present or right is not None
                stream.side('target', right)
                del target_configured, right
                if has_cases:
                    rows = left_rows+right_rows
                    reasons = sorted(filter(None, ['density_contract_unconfirmed', 'unit_area_basis_unconfirmed',
                                                   'same_name_cases_unresolved' if left_unresolved or right_unresolved else '']))
                    stream.commit({'status': 'partial', 'reasons': reasons, 'raw_case_areas_status': 'available',
                                   'classified_line_pixel_areas_status': 'partial' if rows else 'unavailable',
                                   'weighted_rates_status': 'unavailable', 'deltas_status': 'unavailable'}, rows)
                    return {'status': 'partial', 'reasons': reasons,
                            'outputs': [str(output/'comparison.json'), str(output/'comparison.csv')], 'exit_code': 3}
            except Exception as exc:
                # before or after begin(): cli.py's failure handler must not delete the previous good generation
                exc.keeps_previous_outputs = True
                raise
            finally:
                stream.abort()
        output = Path(out) if out is not None else _numeric_path(cfg, cfg['output_dir'])
        rows = []
        for side, imported in (('reference', reference_real[1]), ('target', target_real[1])):
            for case in imported['cases']:
                for record in case['records']:
                    rows.append({'side': side, 'case_id': case['case_name'], 'diameter_um': record['diameter_um'],
                                 'input_status': record['input_status'], 'raw_area_um2': record['png_area_um2'],
                                 'csv_area_um2': (record.get('csv_area') or {}).get('value'),
                                 'csv_raw_value': (record.get('csv_area') or {}).get('raw_area'),
                                 'csv_raw_diameter': (record.get('csv_area') or {}).get('raw_diameter'),
                                 'csv_status': record.get('csv_status'),
                                 'area_difference_um2': record.get('area_difference_um2'),
                                 'area_tolerance_um2': record.get('area_tolerance_um2'),
                                 'classification_status': case['classification_status'],
                                 'reason': record.get('reason') or record.get('csv_status')})
        payload = {'schema_version': 'real_hotspot_comparison_v1', 'source_contract_version': 'real_hotspot_v1',
                   'is_synthetic': False, 'status': 'partial', 'reasons': ['explicit_condition_assignments_required',
                   'pair_classification_table_required', 'density_and_unit_metadata_missing'],
                   'raw_case_areas_status': 'available', 'classified_comparison_status': 'unavailable',
                   'line_pixel_rates_status': 'unavailable',
                   'deltas_status': 'unavailable', 'records': rows,
                   'reference_source_hash': reference_real[1]['source_payload_hash'],
                   'target_source_hash': target_real[1]['source_payload_hash']}
        if any(case['diagnostics'] for source in (reference_real[1], target_real[1]) for case in source['cases']):
            payload['reasons'] = sorted(set((*payload['reasons'], 'same_name_endpoints_unresolved')))
        try:paths = _write_inspection(output, 'comparison', payload, rows)
        except Exception as exc:
            exc.keeps_previous_outputs = False  # non-transactional writer: a partial write must not stay beside old files
            raise
        return {'status': 'partial', 'reasons': payload['reasons'], 'outputs': paths, 'exit_code': 3}
    _,reference = _numeric_input(cfg,cfg.get('reference'))
    _,target = _numeric_input(cfg,cfg.get('target'))
    rr = _numeric_rates(reference,cfg['reference'])
    tr = _numeric_rates(target,cfg['target'])
    deltas = compare_rates(rr,tr,reference_product_id=cfg['reference']['product_id'],
                           target_product_id=cfg['target']['product_id'])
    payload,tables = comparison_report(rr,tr,deltas)
    output = Path(out) if out is not None else _numeric_path(cfg,cfg['output_dir'])
    try:paths = write_numeric_report(output,'comparison',payload,tables)
    except Exception as exc:
        exc.keeps_previous_outputs = False  # non-transactional writer (see the real inspection branch)
        raise
    return {'status':payload['status'],'reasons':payload['reasons'],'outputs':paths,'exit_code':0 if payload['status']=='available' else 3}


def _map_spec(cfg):
    from simcompare.numeric_schema import NumericContractError
    spec = cfg.get('map')
    if not isinstance(spec,dict) or set(spec)-{'input','diameter_um','min_count','per_cell_text'}:
        raise NumericContractError('numeric_map_config_required')
    if type(spec.get('per_cell_text',False)) is not bool:
        raise NumericContractError('invalid_numeric_config','map.per_cell_text는 boolean이어야 합니다')
    from .numeric_schema import DIAMETERS_UM
    if spec.get('diameter_um',5) not in DIAMETERS_UM:
        raise NumericContractError('invalid_representative_diameter', '지름은 1..30, 40, 50 um 중 하나여야 합니다')
    return spec


def _numeric_map(cfg):
    from simcompare.electrode_map import compute_electrode_maps
    spec = _map_spec(cfg)
    root,dataset = _numeric_input(cfg,spec.get('input'))
    maps = compute_electrode_maps(dataset,diameter_um=spec.get('diameter_um',5),min_count=spec.get('min_count'))
    return root,dataset,maps


def run_numeric_map(cfg, out=None):
    from simcompare.report import map_report, write_numeric_report
    spec = cfg.get('map')
    if isinstance(spec, dict) and isinstance(spec.get('input'), dict):
        real = _real_source(cfg, spec['input'])
        if real is not None:
            configured = _configured_real(cfg, spec['input'], real)
            if configured is not None:
                from simcompare.electrode_map import compute_electrode_maps
                from simcompare.report import map_report, write_map_arrays, write_numeric_report
                dataset = configured[1]
                if not dataset.cases:
                    rows = [{'case_id': name, 'status': 'unresolved_same_name_endpoints'}
                            for name in dataset.provenance.get('unresolved_same_name_cases', [])]
                    payload = {'schema_version': 'real_hotspot_electrode_map_v1', 'is_synthetic': False,
                               'status': 'unavailable', 'reasons': ['same_name_endpoints_unresolved'],
                               'maps': [], 'source_cases': rows,
                               'source_payload_hash': dataset.provenance.get('source_payload_hash')}
                    paths = _write_inspection(Path(out) if out is not None else _numeric_path(cfg, cfg['output_dir']),
                                              'electrode_map', payload, rows)
                    return {'status': 'unavailable', 'reasons': payload['reasons'], 'outputs': paths, 'exit_code': 3}
                maps = compute_electrode_maps(dataset, diameter_um=spec.get('diameter_um', 5),
                                              min_count=spec.get('min_count'))
                payload, tables = map_report(maps, per_cell=spec.get('per_cell_text', False))
                payload['is_synthetic'] = False
                if dataset.provenance.get('unresolved_same_name_cases'):
                    payload['reasons'] = sorted(set((*payload.get('reasons', ()), 'same_name_cases_unresolved')))
                    payload['status'] = 'partial'
                output = Path(out) if out is not None else _numeric_path(cfg, cfg['output_dir'])
                output.mkdir(parents=True, exist_ok=True)
                for old in output.glob('electrode_map*'):
                    if old.is_file() and old.suffix in ('.json', '.html', '.csv', '.npz', '.png'):
                        old.unlink()
                from simcompare.heatmap import write_heatmaps
                payload['heatmaps'] = write_heatmaps(output, maps)
                paths = write_map_arrays(output, maps, payload)
                paths += write_numeric_report(output, 'electrode_map', payload, tables)
                paths += [str(output/ref[k]) for ref in payload['heatmaps'] for k in ('png', 'sidecar') if ref[k] is not None]
                return {'status': payload['status'], 'reasons': payload.get('reasons', []), 'outputs': paths, 'exit_code': 3}
            output = Path(out) if out is not None else _numeric_path(cfg, cfg['output_dir'])
            manifest = real[1]
            rows = [{'case_id': case['case_name'], 'electrode_a': case['endpoints'][0]['electrode_id'],
                     'electrode_b': case['endpoints'][1]['electrode_id'], 'status': case['map_status'],
                     'reason': 'explicit_condition_assignment_required'} for case in manifest['cases']]
            payload = {'schema_version': 'real_hotspot_electrode_map_v1', 'is_synthetic': False,
                       'status': 'unavailable', 'reasons': ['explicit_condition_assignment_required'], 'maps': [], 'source_cases': rows,
                       'source_payload_hash': manifest['source_payload_hash']}
            paths = _write_inspection(output, 'electrode_map', payload, rows)
            return {'status': 'unavailable', 'reasons': payload['reasons'], 'outputs': paths, 'exit_code': 3}
    _,dataset,maps = _numeric_map(cfg)
    if any(m.status!='available' for m in (*maps.layers,*maps.summaries)):
        from .numeric_schema import NumericContractError
        raise NumericContractError('electrode_map_incomplete', ', '.join(sorted({r for m in (*maps.layers,*maps.summaries) for r in m.reasons})))
    payload,tables = map_report(maps,per_cell=cfg['map'].get('per_cell_text',False))
    output = Path(out) if out is not None else _numeric_path(cfg,cfg['output_dir'])
    for old in output.glob('electrode_map*'):
        if old.is_file() and old.suffix in ('.json','.html','.csv','.npz','.png'):old.unlink()
    from simcompare.heatmap import write_heatmaps
    heatmaps = write_heatmaps(output, maps)
    payload['heatmaps'] = heatmaps
    payload['is_synthetic']=bool(dataset.provenance.get('is_synthetic',False))
    from .report import write_map_arrays
    paths=write_map_arrays(output,maps,payload)
    paths += write_numeric_report(output,'electrode_map',payload,tables)
    paths += [str(output/ref[k]) for ref in heatmaps for k in ('png','sidecar') if ref[k] is not None]
    usable = any(m.coverage['source_coverage_status'] in ('available','partial') for m in maps.layers)
    return {'status':payload['status'],'reasons':payload['reasons'],'outputs':paths,'exit_code':0 if payload['status']=='available' else 3 if usable else 2}


def run_numeric_validate(cfg, out=None):
    from simcompare.adapters.real_hotspot import configured_dataset
    from simcompare.measurement import measure_full_mask
    from simcompare.numeric_schema import current_gate_decisions
    from simcompare.report import write_numeric_report
    sections = [(name, cfg[name]) for name in ('reference','target') if name in cfg]
    if 'map' in cfg:
        sections.append(('map.input', cfg['map']['input']))
    if not sections:
        from simcompare.numeric_schema import NumericContractError
        raise NumericContractError('numeric_input_required')
    records,seen,reasons,diagnostics = [],set(),set(),[]
    input_aliases = {}
    saw_real = False
    real_physical_metadata = None
    expected_coverage = None
    for label, section in sections:
        try:
            real = _real_source(cfg, section)
            if real is not None:
                saw_real = True
                root, manifest = real
                if real_physical_metadata is None:
                    real_physical_metadata = manifest['physical_metadata']
                    expected_coverage = manifest['expected_case_coverage_status']
                assignment = section.get('condition_assignment')
                if assignment is not None:
                    # validate-data admits what the following compare would admit (same bucket detail)
                    full_detail, top = _compare_options(cfg)
                    configured_dataset(root, manifest, assignment, top_buckets=None if full_detail else top)
                source_key = str(root.resolve()) + '::' + str(section.get('manifest_path', 'manifest.json'))
                input_aliases.setdefault(source_key, []).append(label)
                if source_key not in seen:
                    seen.add(source_key)
                    # Raw manifest rows remain visible once per physical input; labels
                    # preserve which config sections refer to each source.
                    for case in manifest['cases']:
                        for record in case['records']:
                            records.append({'input': label, 'case_id': case['case_name'],
                                'diameter_um': record['diameter_um'], 'failure_type': case['failure_type'],
                                'input_status': record['input_status'], 'area_um2': record.get('png_area_um2'),
                                'csv_area_um2': (record.get('csv_area') or {}).get('value'),
                                'csv_raw_value': (record.get('csv_area') or {}).get('raw_area'),
                                'csv_raw_diameter': (record.get('csv_area') or {}).get('raw_diameter'),
                                'csv_status': record.get('csv_status'),
                                'area_difference_um2': record.get('area_difference_um2'),
                                'area_tolerance_um2': record.get('area_tolerance_um2'),
                                'geometry_status': case.get('geometry_status'),
                                'status': 'partial' if record.get('csv_status') == 'mismatch' or case.get('geometry_status') != 'confirmed' else record['input_status'],
                                'reason': record.get('reason') or record.get('csv_status')})
                            if record.get('reason'):
                                reasons.add(record['reason'])
                            if record.get('csv_status') not in (None, 'consistent'):
                                reasons.add('csv_'+record['csv_status'])
                    reasons.update(d.get('status', '') for d in manifest.get('diagnostics', []))
                continue
            root,dataset = _numeric_input(cfg,section)
            source_key = str(root.resolve()) + '::' + str(section.get('manifest_path', 'manifest.json'))
            input_aliases.setdefault(source_key, []).append(label)
            if source_key in seen:
                continue
            seen.add(source_key)
            for case in dataset.cases:
                for d,record in sorted(case.records.items()):
                    measured = measure_full_mask(record)
                    record_reasons = sorted(set((*measured.reasons,*case.reasons)))
                    reasons.update(record_reasons)
                    records.append({'input': label,'product_id':case.identity.product_id,'case_id':case.identity.case_id,
                                    'diameter_um':d,'failure_type':case.failure_type,'input_status':record.input_status,
                                    'value':measured.ca_um2,'partial_value':measured.partial_value,'unit':'um^2','status':measured.status,
                                    'reasons':record_reasons,'measurement':measured})
            reasons.update(dataset.reasons)
        except Exception as exc:
            if cfg.get('mode') != 'real':
                raise
            message = f'{label}: {type(exc).__name__}: {exc}'
            diagnostics.append({'input': label, 'status': 'invalid', 'error': str(exc)})
            reasons.add('invalid_configured_input')
            records.append({'input': label, 'status': 'unavailable', 'reason': message})
    payload = {'schema_version':'numeric_validation_v1','status':'partial','reasons':sorted(reasons),'records':records,
               'diagnostics':diagnostics,'gates':current_gate_decisions(),
               'input_aliases': {key: values for key, values in sorted(input_aliases.items())}}
    if saw_real:
        payload['is_synthetic'] = False
        payload['physical_metadata'] = real_physical_metadata
        payload['expected_case_coverage_status'] = expected_coverage
        payload['raw_observations_status'] = 'available' if any(r.get('area_um2') is not None for r in records) else 'unavailable'
        payload['classified_comparison_status'] = 'unavailable'
        payload['map_status'] = 'requires_explicit_condition_assignment'
    output = Path(out) if out is not None else _numeric_path(cfg,cfg['output_dir'])
    paths = write_numeric_report(output,'validation',payload,{'records':records})
    return {'status':'partial','reasons':sorted(reasons),'outputs':paths,'exit_code':3}


_ELECTRODE_SEMANTICS = ('binary', 'binary_zero_is_marked')


def _json_bytes_unique(data):
    """roi_export._json_load와 같은 중복 key·비유한 수 검사로 이미 읽은 bytes를 파싱한다."""
    from simcompare.numeric_schema import NumericContractError

    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise NumericContractError('duplicate_asset_manifest_key', k)
            result[k] = v
        return result

    def constant(value):
        raise NumericContractError('nonfinite_asset_manifest_number', value)
    return json.loads(data.decode('utf-8'), object_pairs_hook=unique, parse_constant=constant)


def _read_verified(path, expected):
    """파일을 한 번만 bytes로 읽어 config의 sha256과 대조하고 같은 bytes를 파싱한다. (파싱 결과, sha256)."""
    from simcompare.numeric_schema import NumericContractError
    data = Path(path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if not isinstance(expected, str) or digest != expected:
        raise NumericContractError('input_hash_mismatch', Path(path).name)
    return _json_bytes_unique(data), digest


def _assert_evidence_unchanged(snapshot):
    """검증한 증거 파일이 읽는 동안 바뀌지 않았는지 다시 해시한다."""
    from simcompare.numeric_schema import NumericContractError
    for path, digest in snapshot:
        try:
            now = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except OSError as exc:
            raise NumericContractError('evidence_changed_during_read', Path(path).name) from exc
        if now != digest:
            raise NumericContractError('evidence_changed_during_read', Path(path).name)


def _electrode_evidence(root, section):
    """real_hotspot_v1 전극 asset 수용 증거를 검사한다. 극성은 추측하지 않는다.

    반환: None(증거 없음 → 기존 unavailable 단락), ('blocked', reasons), 또는 ('accepted', manifest).
    hash·binding 불일치는 NumericContractError(이름 있는 오류)다.
    """
    from simcompare.adapters.electrode_png import SOURCE_KIND_CONTRACT
    from simcompare.numeric_images import resolve_path
    from simcompare.numeric_schema import NumericContractError
    asset_ref, record_ref = section.get('asset_manifest'), section.get('electrode_import_record')
    if asset_ref is None or record_ref is None or section.get('condition_assignment') is None:
        return None
    for name, ref in (('asset_manifest', asset_ref), ('electrode_import_record', record_ref)):
        if (not isinstance(ref, dict) or set(ref) != {'path', 'sha256'}
                or not all(isinstance(ref[k], str) and ref[k] for k in ref)):
            raise NumericContractError('invalid_numeric_config', name + '는 {path, sha256} 객체여야 합니다')
    record_path = resolve_path(root, record_ref['path'])
    manifest_path = resolve_path(root, asset_ref['path'])
    try:
        record, record_digest = _read_verified(record_path, record_ref['sha256'])
        manifest, manifest_digest = _read_verified(manifest_path, asset_ref['sha256'])
    except (OSError, ValueError) as exc:
        if getattr(exc, 'code', None) == 'input_hash_mismatch':
            raise
        raise NumericContractError('malformed_asset_manifest', str(exc)) from exc
    if (not isinstance(record, dict) or record.get('schema_version') != 'electrode_asset_import_record_v1'
            or not isinstance(manifest, dict) or manifest.get('schema_version') != 'review_assets_v1'):
        raise NumericContractError('electrode_import_record_invalid')
    import_info = manifest.get('electrode_asset_import')
    if not isinstance(import_info, dict):
        return None
    if record.get('polarity_basis') is None or import_info.get('polarity_basis') is None:
        return None
    if (record.get('manifest_sha256') != asset_ref['sha256'] or import_info.get('record') != record_path.name
            or record.get('dataset_input_hash') != manifest.get('source_manifest_hash')
            or record.get('source_kind') != import_info.get('source_kind')):
        raise NumericContractError('electrode_import_record_mismatch')
    contract = SOURCE_KIND_CONTRACT.get(record['source_kind']) if isinstance(record['source_kind'], str) else None
    if contract is None or record['polarity_basis'] != contract['polarity_basis'] \
            or import_info['polarity_basis'] != contract['polarity_basis']:
        return 'blocked', ['electrode_polarity_unspecified']
    refs = []
    try:
        for entry in manifest['cases']:
            assets = entry['assets']
            refs += [item['asset'] for item in assets['layouts']] + [assets['valid'], assets['cnt'], *assets.get('via', [])]
    except (KeyError, TypeError):
        raise NumericContractError('source_asset_manifest_invalid') from None
    if not refs or any(not isinstance(r, dict) or r.get('value_semantics') not in _ELECTRODE_SEMANTICS for r in refs):
        return 'blocked', ['electrode_polarity_unspecified']
    return 'accepted', manifest, ((record_path, record_digest), (manifest_path, manifest_digest))


def _real_roi_export(cfg, out, real, spec, prepared=None):
    """실자료 export: configured_dataset과 electrode-map 영역, 수용된 review_assets_v1만 쓴다.

    prepared=(root, dataset, maps)이면 호출자(candidates)가 이미 계산한 값을 재사용한다.
    """
    from simcompare.electrode_map import compute_electrode_maps
    from simcompare.numeric_schema import NumericContractError
    from simcompare.roi_export import export_review_rois
    evidence = _electrode_evidence(real[0], spec['input'])
    if evidence is None:
        return None
    if evidence[0] == 'blocked':
        payload = {'schema_version': 'review_roi_v1', 'producer_commit': None,
                   'source_manifest_hash': real[1]['source_payload_hash'], 'assets_root': str(real[0]),
                   'rois': [], 'excluded_records': [], 'status': 'blocked', 'reasons': evidence[1]}
        paths = _write_inspection(Path(out), 'review_rois', payload, [])
        return {'status': 'blocked', 'reasons': evidence[1], 'outputs': paths, 'exit_code': 3}
    if prepared is None:
        _map_spec(cfg)
        root, dataset = _configured_real(cfg, spec['input'], real)
    else:
        root, dataset, prepared_maps = prepared
    if evidence[1].get('source_manifest_hash') != dataset.provenance['input_hash']:
        raise NumericContractError('source_asset_manifest_identity_mismatch')
    if not dataset.cases:
        payload = {'schema_version': 'review_roi_v1', 'producer_commit': None,
                   'source_manifest_hash': dataset.provenance.get('source_payload_hash'), 'assets_root': str(root),
                   'rois': [], 'excluded_records': [], 'status': 'unavailable',
                   'reasons': ['same_name_endpoints_unresolved']}
        paths = _write_inspection(Path(out), 'review_rois', payload, [])
        return {'status': 'unavailable', 'reasons': payload['reasons'], 'outputs': paths, 'exit_code': 3}
    maps = prepared_maps if prepared is not None else compute_electrode_maps(
        dataset, diameter_um=spec.get('diameter_um', 5), min_count=spec.get('min_count'))
    # export_review_rois는 manifest를 경로로 다시 읽는다. 검증한 bytes와 같은지 읽기 전후로 확인한다.
    _assert_evidence_unchanged(evidence[2])
    result = export_review_rois(dataset, root, out, maps=maps, selections=cfg.get('review_rois', {}),
                                asset_manifest=spec['input'].get('asset_manifest'))
    _assert_evidence_unchanged(evidence[2])
    return result


def run_numeric_roi_export(cfg, out):
    if isinstance(cfg.get('map'), dict) and isinstance(cfg['map'].get('input'), dict):
        real = _real_source(cfg, cfg['map']['input'])
        if real is not None:
            accepted = _real_roi_export(cfg, out, real, cfg['map'])
            if accepted is not None:
                return accepted
            from simcompare.numeric_schema import NumericContractError
            payload = {'schema_version': 'review_roi_v1', 'producer_commit': None,
                       'source_manifest_hash': real[1]['source_payload_hash'], 'assets_root': str(real[0]),
                       'rois': [], 'excluded_records': [], 'status': 'unavailable',
                       'reasons': ['electrode_polarity_unknown', 'source_layout_assets_unconfirmed']}
            paths = _write_inspection(Path(out), 'review_rois', payload, [])
            return {'status': 'unavailable', 'reasons': payload['reasons'], 'outputs': paths, 'exit_code': 3}
    from simcompare.roi_export import export_review_rois
    root,dataset,maps = _numeric_map(cfg)
    return export_review_rois(dataset,root,out,maps=maps,selections=cfg.get('review_rois',{}),
                              asset_manifest=cfg['map']['input'].get('asset_manifest'))


def _dataset_for(cfg, section, label):
    """numeric_v2 dataset, or the explicitly configured real_hotspot_v1 dataset."""
    from simcompare.numeric_schema import NumericContractError
    real = _real_source(cfg, section)
    if real is not None:
        configured = _configured_real(cfg, section, real)
        if configured is None:
            raise NumericContractError('explicit_condition_assignments_required', label)
        return configured[1]
    return _numeric_input(cfg, section)[1]


def _stage6_artefact_candidate(cfg, reference, target):
    """(output, reason): the compare output folder when its comparison_masks.zip may be read, else (None, reason).

    Only stats the file: nothing of the artefact is read here, so the stage-6 admission can run before the read (RB-A5).
    Real datasets only (the artefact is written by the real compare path); the size is checked against the operation limit."""
    from simcompare.adapters.real_hotspot import _dataset_profile
    from simcompare.compare_masks import artefact_bytes
    if cfg.get('output_dir') is None or any(_dataset_profile(d) is None for d in (reference, target)):
        return None, 'not_real'
    output = _numeric_path(cfg, cfg['output_dir'])
    size = artefact_bytes(output)
    if size is None:
        return None, 'absent'
    if size > min(_dataset_profile(d)[3] for d in (reference, target)):
        return None, 'artefact_exceeds_budget'
    return output, None


def _load_stage6_artefact(output, reference, target):
    """(areas, reason): the packed masks stage 2 published when they match both models, else (None, reason)."""
    from simcompare.adapters.real_hotspot import _dataset_profile
    from simcompare.compare_masks import load_compare_masks
    limit = min(_dataset_profile(d)[3] for d in (reference, target))
    a, b, reason = load_compare_masks(output, reference, target, max_bytes=limit)
    return ((a, b) if reason is None else None), reason


def run_numeric_local_delta(cfg, out, *, correspondence, window_sizes_um, jitter_um=None, sort_diameter_um=None,
                            compact_json=False, config_path=None):
    """compare-windows: same physical window around corresponding positions, b − a."""
    from simcompare.correspondence import bind_pairs, load_correspondence
    from simcompare.local_delta import compute_local_delta, correspondence_hash, write_local_delta
    from simcompare.local_delta_report import write_local_delta_html
    from simcompare.numeric_schema import NumericContractError, finite
    from simcompare.publication import SetPublication
    real = [_real_source(cfg, cfg.get(name)) is not None for name in ('reference', 'target')]
    if any(real) and not all(real):
        raise NumericContractError('mixed_real_and_numeric_source_formats')
    reference = _dataset_for(cfg, cfg.get('reference'), 'reference')
    target = _dataset_for(cfg, cfg.get('target'), 'target')
    from simcompare.adapters.real_hotspot import guard_stage_compare_windows
    from simcompare.local_delta import _validate_sizes
    payload = load_correspondence(correspondence)
    pairs = bind_pairs(payload, reference, target)
    sizes = _validate_sizes(window_sizes_um)
    if jitter_um is not None:
        jitter_um = finite(jitter_um, 'jitter', positive=True)
    # Q3: the packed masks stage 2 published, when they match both models; anything else recomputes compute_areas.
    # B3 / RB-A5: admission after the pairs are bound, before any compute, decode or cache allocation, and before the
    # artefact is read (the reuse estimate prices its packed masks); a candidate that turns out unusable is admitted again
    # as a recompute before compute_areas runs.
    output_dir, _ = _stage6_artefact_candidate(cfg, reference, target)
    guard_stage_compare_windows('compare-windows', reference, target, layouts_cfg=cfg, pairs=pairs, sizes=sizes,
                                jitter_um=jitter_um, reuse=output_dir is not None)
    areas = None
    if output_dir is not None:
        areas, _ = _load_stage6_artefact(output_dir, reference, target)
        if areas is None:
            guard_stage_compare_windows('compare-windows', reference, target, layouts_cfg=cfg, pairs=pairs, sizes=sizes,
                                        jitter_um=jitter_um, reuse=False)
    # B2: the whole produced set (json, csv, html, png) is staged and published as one generation (publication.py); a
    # failure anywhere before its commit leaves the previous generation exactly as it was. begin() runs before the
    # compute so that an unreadable earlier publication is reported before the expensive work.
    output = Path(out) if out is not None else _numeric_path(cfg, cfg['output_dir'])
    publication = SetPublication(output)
    try:
        staging = publication.begin()
        result = compute_local_delta(pairs, reference, target, window_sizes_um=window_sizes_um, jitter_um=jitter_um,
                                     sort_diameter_um=sort_diameter_um, areas=areas,
                                     provenance={'correspondence_path': str(Path(correspondence).resolve()),
                                                 'correspondence_sha256': correspondence_hash(correspondence),
                                                 'correspondence_producer': payload['producer'],
                                                 'correspondence_warnings': payload['warnings'],
                                                 'config_path': str(cfg.get('_config_path', config_path or '')) or None,
                                                 'config_sha256': cfg.get('_config_sha256')})
        paths = write_local_delta(staging, result, compact=compact_json)
        paths += write_local_delta_html(staging, result, cfg=cfg, reference=reference, target=target)
        publication.commit()
    except Exception as exc:
        # from here on the previous generation is intact or being recovered (publication.py): tell cli.py's failure
        # handler not to delete the managed reports, which would destroy exactly what the transaction preserved
        exc.keeps_previous_outputs = True
        raise
    finally:
        publication.abort()
    paths = [publication.final_path(path) for path in paths]
    return {'status': result.status, 'reasons': list(result.reasons), 'outputs': paths,
            'exit_code': 0 if result.status == 'available' else 3}

