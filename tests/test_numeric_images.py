from pathlib import Path
import hashlib
import struct
import zlib

from png_helpers import binary_rows, png_chunk, raw_png

import numpy as np
from PIL import Image
import pytest

from simcompare.numeric_images import read_binary, resolve_path
from simcompare.numeric_schema import Frame, MaskRecord, NumericContractError, NumericPathError
from simcompare.measurement import measure_full_mask

FIXTURE=Path(__file__).parent/'fixtures/numeric_v2'


@pytest.mark.parametrize('name',['mask.png','mask_bool.npy','mask_01.npy','mask_255.npy'])
def test_encodings_have_identical_hand_calculated_ca(name):
    mask=read_binary(FIXTURE,name)
    assert mask.tolist()==[[True,False],[False,True]]
    frame=Frame('f',0.5,(0,0),(2,2),[[1,1],[1,1]])
    measured=measure_full_mask(MaskRecord(5,frame,mask))
    assert measured.ca_um2==0.5
    assert measured.valid_area_um2==1
    assert measured.status=='available'


@pytest.mark.parametrize('value', [np.array([[np.nan]]),np.array([[-1]]),np.ones((1,1,1)),
    np.array([[0.5]]),np.array([[255.0]]),np.array([[1,255]],dtype='uint8'),
    np.array([[1+0j]]),np.empty((0,1))])
def test_invalid_nominal_arrays_are_not_thresholded(tmp_path,value):
    np.save(tmp_path/'bad.npy',value)
    with pytest.raises(NumericContractError):
        read_binary(tmp_path,'bad.npy')


def test_pickle_rejected(tmp_path):
    np.save(tmp_path/'bad.npy',np.array([[object()]],dtype=object))
    with pytest.raises(NumericContractError):
        read_binary(tmp_path,'bad.npy')


@pytest.mark.parametrize('path',['../mask.npy','/tmp/mask.npy','C:\\mask.npy','..\\mask.npy'])
def test_absolute_and_relative_escapes_rejected(tmp_path,path):
    with pytest.raises(NumericPathError):
        resolve_path(tmp_path,path)


def test_symlink_escape_rejected(tmp_path):
    root=tmp_path/'root';root.mkdir()
    np.save(tmp_path/'outside.npy',np.array([[1]]))
    (root/'link.npy').symlink_to(tmp_path/'outside.npy')
    with pytest.raises(NumericPathError):
        read_binary(root,'link.npy')


def test_probability_semantics_not_promoted_even_if_values_happen_to_be_binary():
    with pytest.raises(NumericContractError,match='unsupported_mask_semantics'):
        read_binary(FIXTURE,'mask.png',value_semantics='probability')


def test_black_hotspot_png_is_read_as_marked_and_gives_the_hand_calculated_area(tmp_path):
    # 2x3 image, hotspot drawn black (0) on white (255): 2 black cells.
    Image.fromarray(np.array([[0,255,255],[255,0,255]],dtype=np.uint8)).save(tmp_path/'black.png')
    marked=read_binary(tmp_path,'black.png',value_semantics='binary_zero_is_marked')
    assert marked.tolist()==[[True,False,False],[False,True,False]]
    assert not marked.flags.writeable
    # Without the declaration the same file is read the other way round: 4 cells, not 2.
    assert int(read_binary(tmp_path,'black.png').sum())==4


def test_color_and_fractional_png_not_converted(tmp_path):
    Image.fromarray(np.array([[[0,255,0]]],dtype='uint8')).save(tmp_path/'rgb.png')
    Image.fromarray(np.array([[128]],dtype='uint8')).save(tmp_path/'gray.png')
    for name in ['rgb.png','gray.png']:
        with pytest.raises(NumericContractError):
            read_binary(tmp_path,name)


def test_asset_hash_checked():
    with pytest.raises(NumericContractError,match='input_hash_mismatch'):
        read_binary(FIXTURE,'mask.png',sha256='0'*64)
    digest=hashlib.sha256((FIXTURE/'mask.png').read_bytes()).hexdigest()
    assert read_binary(FIXTURE,'mask.png',sha256=digest).sum()==2
def test_real_asset_requires_explicit_polarity(tmp_path):
    import numpy as np
    import pytest
    from simcompare.numeric_images import read_binary
    from simcompare.numeric_schema import NumericContractError
    np.save(tmp_path/'black.npy',np.array([[0,255]],dtype=np.uint8))
    with pytest.raises(NumericContractError,match='explicit_value_semantics_required'):
        read_binary(tmp_path,'black.npy',synthetic=False)
    assert read_binary(tmp_path,'black.npy',synthetic=False,value_semantics='binary_zero_is_marked').tolist()==[[True,False]]
    assert read_binary(tmp_path,'black.npy',synthetic=True).tolist()==[[False,True]]


SUPPORTED = [(0, 1), (0, 2), (0, 4), (0, 8), (2, 8),
             (3, 1), (3, 2), (3, 4), (3, 8), (4, 8), (6, 8)]
WHITE = [[True, False, False], [False, True, False]]


def rejection(tmp_path, data, reason, monkeypatch=None):
    path = tmp_path/'nested/bad.png'; path.parent.mkdir(exist_ok=True)
    path.write_bytes(data)
    if monkeypatch is not None:
        def forbidden(*args, **kwargs):
            pytest.fail('invalid structure reached Pillow before rejection')
        monkeypatch.setattr(Image, 'open', forbidden)
    with pytest.raises(NumericContractError) as error:
        read_binary(tmp_path, 'nested/bad.png')
    assert error.value.code == reason
    assert 'nested/bad.png' in str(error.value)


@pytest.mark.parametrize('colour,depth', SUPPORTED)
@pytest.mark.parametrize('interlace', [0, 1])
@pytest.mark.parametrize('semantics', ['binary', 'binary_zero_is_marked'])
def test_all_png_types_and_adam7_passes_keep_declared_polarity(tmp_path, colour, depth, interlace, semantics):
    # 9x9 ensures every Adam7 pass has a real row and column; asymmetric pattern.
    white = [[(x + 2*y) % 3 == 0 for x in range(9)] for y in range(9)]
    palette = [(b'PLTE', b'\0\0\0\xff\xff\xff')] if colour == 3 else []
    data = raw_png(9, 9, depth, colour, binary_rows(white, depth, colour, interlace=interlace),
                   interlace=interlace, before=palette)
    (tmp_path/'valid.png').write_bytes(data)
    actual = read_binary(tmp_path, 'valid.png', value_semantics=semantics)
    expected = white if semantics == 'binary' else [[not v for v in row] for row in white]
    assert actual.tolist() == expected
    assert not actual.flags.writeable


@pytest.mark.parametrize('mode,bits', [('1', None), ('L', None), ('RGB', None), ('LA', None),
                                      ('RGBA', None), ('P', 1), ('P', 2), ('P', 4), ('P', 8)])
@pytest.mark.parametrize('interlace_option', [0, 1])
@pytest.mark.parametrize('semantics', ['binary', 'binary_zero_is_marked'])
def test_pillow_written_png_modes_and_ancillary_metadata(tmp_path, mode, bits, interlace_option, semantics):
    from PIL.PngImagePlugin import PngInfo
    image = Image.fromarray(np.array(WHITE, dtype=np.uint8)*255)
    if mode == 'P':
        image = Image.fromarray(np.array(WHITE, dtype=np.uint8)).convert('P')
        image.putpalette([0, 0, 0, 255, 255, 255]+[0]*762)
    else:
        image = image.convert(mode)
    info = PngInfo(); info.add_text('purpose', 'binary fixture'); info.add_text('zipped', 'metadata', zip=True)
    info.add_itxt('note', '합성')
    exif = Image.Exif(); exif[274] = 1
    options = {'pnginfo': info, 'exif': exif, 'interlace': interlace_option}
    if bits is not None:
        options['bits'] = bits
    path = tmp_path/'pillow.png'; image.save(path, **options)
    # Pillow does not implement PNG Adam7 writing; the independent matrix above does.
    assert path.read_bytes()[28] == 0
    expected = WHITE if semantics == 'binary' else [[not v for v in row] for row in WHITE]
    assert read_binary(tmp_path, 'pillow.png', value_semantics=semantics).tolist() == expected


@pytest.mark.parametrize('semantics', ['binary', 'binary_zero_is_marked'])
@pytest.mark.parametrize('values', [[[0, 1, 1]], [[1, 1, 1]], [[0, 0, 0]], [[0, 255, 255]]])
def test_8bit_single_channel_01_compatibility(tmp_path, semantics, values):
    Image.fromarray(np.array(values, dtype=np.uint8)).save(tmp_path/'legacy.png')
    expected = [[bool(v) if semantics == 'binary' else not bool(v) for v in values[0]]]
    assert read_binary(tmp_path, 'legacy.png', value_semantics=semantics).tolist() == expected


@pytest.mark.parametrize('rows', [63, 32, 1, 0])
@pytest.mark.parametrize('depth', [1, 8])
def test_review_short_idat_rows_rejected_before_decode(tmp_path, monkeypatch, rows, depth):
    raw = (b'\0'+b'\xff'*(8 if depth == 1 else 64))*rows
    rejection(tmp_path, raw_png(64, 64, depth, 0, raw), 'png_idat_length_mismatch', monkeypatch)


@pytest.mark.parametrize('depth', [1, 2, 4, 8])
@pytest.mark.parametrize('sample', [0, 1])
def test_review_grey_trns_even_unused_is_rejected(tmp_path, monkeypatch, depth, sample):
    packed = {1: b'\x60', 2: b'\x3c', 4: b'\x0f\xf0', 8: b'\0\xff\xff\0'}[depth]
    rejection(tmp_path, raw_png(4, 1, depth, 0, b'\0'+packed,
              before=[(b'tRNS', struct.pack('>H', sample))]), 'png_nonopaque_transparency', monkeypatch)


def test_rgb_trns_even_unused_is_rejected(tmp_path, monkeypatch):
    rejection(tmp_path, raw_png(1, 1, 8, 2, b'\0\xff\xff\xff',
              before=[(b'tRNS', struct.pack('>HHH', 1, 2, 3))]), 'png_nonopaque_transparency', monkeypatch)


@pytest.mark.parametrize('depth,entries,packed', [(1, 1, b'\x80'), (2, 2, b'\x80'),
                                                  (4, 2, b'\x20'), (8, 2, b'\x02')])
def test_review_palette_index_equal_entries_is_rejected(tmp_path, monkeypatch, depth, entries, packed):
    rejection(tmp_path, raw_png(1, 1, depth, 3, b'\0'+packed,
              before=[(b'PLTE', b'\0\0\0'*entries)]), 'png_palette_index_out_of_range', monkeypatch)


def test_review_missing_plte_is_rejected(tmp_path, monkeypatch):
    rejection(tmp_path, raw_png(1, 1, 8, 3, b'\0\0'), 'png_missing_palette', monkeypatch)


@pytest.mark.parametrize('shape', [(8, 4, 0, 0), (8, 7, 0, 1), (1, 1, 7, 7), (8, 8, 0, 0)])
def test_review_apng_subregions_rejected_before_decode(tmp_path, monkeypatch, shape):
    control = struct.pack('>IIIIIHHBB', 0, *shape, 1, 100, 0, 0)
    rejection(tmp_path, raw_png(8, 8, 8, 0, (b'\0'+b'\xff'*8)*8,
              before=[(b'acTL', struct.pack('>II', 1, 0)), (b'fcTL', control)]), 'png_apng_unsupported', monkeypatch)


@pytest.mark.parametrize('chunk', [b'acTL', b'fcTL', b'fdAT'])
@pytest.mark.parametrize('position', ['before', 'after'])
def test_all_apng_chunk_types_are_rejected(tmp_path, monkeypatch, chunk, position):
    rejection(tmp_path, raw_png(1, 1, 8, 0, b'\0\xff', **{position: [(chunk, b'')]}),
              'png_apng_unsupported', monkeypatch)


@pytest.mark.parametrize('depth', [1, 2, 4, 8])
def test_unused_nonopaque_palette_entry_is_rejected(tmp_path, monkeypatch, depth):
    rejection(tmp_path, raw_png(1, 1, depth, 3, b'\0\0',
              before=[(b'PLTE', b'\0\0\0\xff\xff\xff'), (b'tRNS', b'\xff\xfe')]),
              'png_nonopaque_transparency', monkeypatch)


@pytest.mark.parametrize('colour', [0, 2, 4, 6])
def test_16bit_png_rejected_before_pillow_can_downconvert(tmp_path, monkeypatch, colour):
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[colour]
    rejection(tmp_path, raw_png(1, 1, 16, colour, b'\0'+b'\xff\xff'*channels),
              'png_unsupported_bit_depth', monkeypatch)


def test_file_replacement_after_validation_cannot_change_mask_or_hash(tmp_path, monkeypatch):
    import simcompare.png_strict as module
    path = tmp_path/'snapshot.png'
    original = raw_png(3, 2, 8, 0, b'\0\xff\0\0\0\0\xff\0')
    path.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()
    validate = module._validate_png_structure
    def replace(data, **kwargs):
        assert data == original
        validate(data, **kwargs)
        path.write_bytes(raw_png(3, 2, 8, 0, b'\0\0\xff\xff\0\xff\0\xff'))
    monkeypatch.setattr(module, '_validate_png_structure', replace)
    assert read_binary(tmp_path, 'snapshot.png', sha256=digest).tolist() == WHITE
    assert hashlib.sha256(path.read_bytes()).hexdigest() != digest


@pytest.mark.parametrize('case,reason', [
    ('signature', 'png_invalid_signature'), ('ancillary_crc', 'png_crc_mismatch'),
    ('idat_crc', 'png_crc_mismatch'), ('trailing', 'png_trailing_bytes'),
    ('missing_iend', 'png_truncated_chunk'), ('truncated_chunk', 'png_truncated_chunk'),
    ('ihdr_not_first', 'png_invalid_chunk_order'), ('duplicate_ihdr', 'png_invalid_chunk_order'),
    ('no_idat', 'png_invalid_chunk_order'), ('idat_separated', 'png_invalid_chunk_order'),
    ('unknown_critical', 'png_unknown_critical_chunk'), ('reserved_bit', 'png_invalid_chunk'),
    ('zero_width', 'png_invalid_header'), ('depth3', 'png_invalid_header'),
    ('colour5', 'png_invalid_header'), ('interlace2', 'png_invalid_header'),
    ('compression1', 'png_invalid_header'), ('filtering1', 'png_invalid_header'),
    ('long_rows', 'png_idat_length_mismatch'), ('invalid_filter', 'png_invalid_scanline_filter'),
    ('bad_zlib', 'png_invalid_idat_stream'), ('truncated_zlib', 'png_invalid_idat_stream'),
    ('second_zlib', 'png_invalid_idat_stream'), ('plte_on_grey', 'png_invalid_palette'),
    ('too_many_plte', 'png_invalid_palette'), ('duplicate_plte', 'png_invalid_palette'),
    ('trns_after_idat', 'png_invalid_transparency'), ('duplicate_trns', 'png_invalid_transparency'),
])
def test_strict_structure_rejections_have_path_and_precede_pillow(tmp_path, monkeypatch, case, reason):
    header = struct.pack('>IIBBBBB', 1, 1, 8, 0, 0, 0, 0)
    data = raw_png(1, 1, 8, 0, b'\0\xff')
    if case == 'signature': data = b'X'+data[1:]
    elif case == 'ancillary_crc': data = data[:-12]+png_chunk(b'tEXt', b'key\0value', bad_crc=True)+data[-12:]
    elif case == 'idat_crc': data = data[:33]+png_chunk(b'IDAT', zlib.compress(b'\0\xff'), bad_crc=True)+data[-12:]
    elif case == 'trailing': data += b'trailing'
    elif case == 'missing_iend': data = data[:-12]
    elif case == 'truncated_chunk': data = data[:42]
    elif case == 'ihdr_not_first': data = data[:8]+png_chunk(b'tEXt', b'k\0v')+data[8:]
    elif case == 'duplicate_ihdr': data = data[:33]+png_chunk(b'IHDR', header)+data[33:]
    elif case == 'no_idat': data = data[:33]+data[-12:]
    elif case == 'idat_separated': data = data[:-12]+png_chunk(b'tEXt', b'k\0v')+png_chunk(b'IDAT', b'')+data[-12:]
    elif case == 'unknown_critical': data = data[:33]+png_chunk(b'ABCD', b'')+data[33:]
    elif case == 'reserved_bit': data = data[:33]+png_chunk(b'abcd', b'')+data[33:]
    elif case in ('zero_width', 'depth3', 'colour5', 'interlace2', 'compression1', 'filtering1'):
        args = {'width': 1, 'height': 1, 'depth': 8, 'colour': 0, 'raw': b'\0\xff'}
        key, value = {'zero_width': ('width', 0), 'depth3': ('depth', 3), 'colour5': ('colour', 5),
                      'interlace2': ('interlace', 2), 'compression1': ('compression', 1), 'filtering1': ('filtering', 1)}[case]
        args[key] = value; data = raw_png(**args)
    elif case == 'long_rows': data = raw_png(1, 1, 8, 0, b'\0\xff\0')
    elif case == 'invalid_filter': data = raw_png(1, 1, 8, 0, b'\5\xff')
    elif case == 'bad_zlib': data = raw_png(1, 1, 8, 0, b'', parts=[b'invalid'])
    elif case == 'truncated_zlib': data = raw_png(1, 1, 8, 0, b'', parts=[zlib.compress(b'\0\xff')[:-1]])
    elif case == 'second_zlib': data = raw_png(1, 1, 8, 0, b'', parts=[zlib.compress(b'\0\xff')+zlib.compress(b'')])
    elif case == 'plte_on_grey': data = raw_png(1, 1, 8, 0, b'\0\xff', before=[(b'PLTE', b'\0\0\0')])
    else:
        palette = (b'PLTE', b'\0\0\0\xff\xff\xff')
        before, after = [palette], []
        if case == 'too_many_plte': before = [(b'PLTE', b'\0\0\0'*3)]
        elif case == 'duplicate_plte': before *= 2
        elif case == 'trns_after_idat': after = [(b'tRNS', b'\xff')]
        elif case == 'duplicate_trns': before += [(b'tRNS', b'\xff')]*2
        data = raw_png(1, 1, 1, 3, b'\0\0', before=before, after=after)
    rejection(tmp_path, data, reason, monkeypatch)


@pytest.mark.parametrize('mode,pixels,reason', [
    ('L', [[0, 128]], 'nonbinary_nominal_mask'), ('L', [[1, 255]], 'nonbinary_nominal_mask'),
    ('RGB', [[[0, 1, 0]]], 'nonbinary_nominal_mask'),
    ('RGBA', [[[255, 255, 255, 254]]], 'png_nonopaque_pixels'),
    ('LA', [[[255, 0]]], 'png_nonopaque_pixels'),
    ('LA', [[[1, 255]]], 'nonbinary_nominal_mask'),
])
def test_pixel_values_are_exact_and_opaque_without_threshold(tmp_path, mode, pixels, reason):
    image = Image.fromarray(np.array(pixels, dtype=np.uint8), mode=mode)
    path = tmp_path/'value.png'; image.save(path)
    with pytest.raises(NumericContractError) as error:
        read_binary(tmp_path, 'value.png')
    assert error.value.code == reason and 'value.png' in str(error.value)


@pytest.mark.parametrize('carrier', ['exif', 'xmp', 'raw_profile'])
@pytest.mark.parametrize('orientation', [2, 3, 6, 8])
def test_nonidentity_orientation_is_rejected_without_transform(tmp_path, carrier, orientation):
    from PIL.PngImagePlugin import PngInfo
    exif = Image.Exif(); exif[274] = orientation
    options = {}
    if carrier == 'exif': options['exif'] = exif
    elif carrier == 'xmp':
        info = PngInfo(); info.add_itxt('XML:com.adobe.xmp', f'<rdf tiff:Orientation="{orientation}"/>')
        options['pnginfo'] = info
    else:
        payload = exif.tobytes()
        info = PngInfo(); info.add_text('Raw profile type exif', '\nexif\n'+str(len(payload))+'\n'+payload.hex())
        options['pnginfo'] = info
    path = tmp_path/'oriented.png'; Image.fromarray(np.array(WHITE, dtype=np.uint8)*255).save(path, **options)
    with pytest.raises(NumericContractError) as error:
        read_binary(tmp_path, 'oriented.png')
    assert error.value.code == 'png_nonidentity_orientation' and 'oriented.png' in str(error.value)


@pytest.mark.parametrize('limit,reason', [(8, 'png_pixel_limit_warning'), (4, 'png_pixel_limit_error'),
                                        (None, 'png_pixel_limit_disabled')])
def test_installed_pixel_guard_precedes_inflation_and_is_never_raised(tmp_path, monkeypatch, limit, reason):
    from PIL import ImageFile
    monkeypatch.setattr(Image, 'MAX_IMAGE_PIXELS', limit)
    monkeypatch.setattr(ImageFile, 'LOAD_TRUNCATED_IMAGES', True)
    # Junk zlib must not be visited: 3x3 is above both tested limits.
    rejection(tmp_path, raw_png(3, 3, 8, 0, b'', parts=[b'not-zlib']), reason, monkeypatch)
    assert Image.MAX_IMAGE_PIXELS is limit and ImageFile.LOAD_TRUNCATED_IMAGES is True


def test_pillow_bomb_exceptions_and_warnings_become_named_contract_errors(tmp_path, monkeypatch):
    import warnings
    path = tmp_path/'small.png'; path.write_bytes(raw_png(1, 1, 8, 0, b'\0\xff'))
    for exception, code in [(Image.DecompressionBombWarning, 'png_pixel_limit_warning'),
                            (Image.DecompressionBombError, 'png_pixel_limit_error')]:
        def bomb(*args, **kwargs):
            if exception is Image.DecompressionBombWarning:
                warnings.warn('synthetic bomb warning', exception)
            else:
                raise exception('synthetic bomb error')
        monkeypatch.setattr(Image, 'open', bomb)
        with pytest.raises(NumericContractError) as error:
            read_binary(tmp_path, 'small.png')
        assert error.value.code == code and 'small.png' in str(error.value)


@pytest.mark.parametrize('filter_type,first,second', [
    (1, b'\0\1', b'\1\1'), (2, b'\0\1', b'\1\1'),
    (3, b'\0\1', b'\1\1'), (4, b'\0\1', b'\1\1')])
def test_palette_range_check_unfilters_rows(tmp_path, monkeypatch, filter_type, first, second):
    # First row uses valid 0/1; second row reconstructs at least one index 2.
    data = raw_png(2, 2, 8, 3, b'\0'+first+bytes([filter_type])+second,
                   before=[(b'PLTE', b'\0\0\0\xff\xff\xff')])
    rejection(tmp_path, data, 'png_palette_index_out_of_range', monkeypatch)


@pytest.mark.parametrize('depth,packed,expected,palette', [
    (1, b'\x1f', [[False, False, False]], b'\0\0\0'),
    (2, b'\x47', [[False, True, False]], b'\xff\xff\xff\0\0\0'),
    (4, b'\x10\x1f', [[False, True, False]], b'\xff\xff\xff\0\0\0'),
])
def test_palette_padding_bits_do_not_count_as_pixels(tmp_path, depth, packed, expected, palette):
    (tmp_path/'padding.png').write_bytes(raw_png(3, 1, depth, 3, b'\0'+packed, before=[(b'PLTE', palette)]))
    assert read_binary(tmp_path, 'padding.png').tolist() == expected


def test_valid_split_idat_stream_and_opaque_palette_alpha(tmp_path):
    raw = b'\0\1\0\0\0\0\1\0'
    compressed = zlib.compress(raw)
    data = raw_png(3, 2, 8, 3, b'', before=[(b'PLTE', b'\0\0\0\xff\xff\xff'), (b'tRNS', b'\xff\xff')],
                   parts=[b'', *[compressed[i:i+1] for i in range(len(compressed))], b''])
    (tmp_path/'split.png').write_bytes(data)
    assert read_binary(tmp_path, 'split.png').tolist() == WHITE


def test_npy_path_never_calls_png_validator(tmp_path, monkeypatch):
    import simcompare.png_strict as module
    def forbidden(*args, **kwargs): pytest.fail('NPY reached PNG validator')
    monkeypatch.setattr(module, '_validate_png_structure', forbidden)
    path = tmp_path/'mask.npy'; np.save(path, np.array(WHITE, dtype=np.uint8))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert read_binary(tmp_path, 'mask.npy', sha256=digest).tolist() == WHITE
    assert read_binary(tmp_path, 'mask.npy', value_semantics='binary_zero_is_marked').tolist() == [[not v for v in row] for row in WHITE]


@pytest.mark.parametrize('colour,depth', SUPPORTED)
@pytest.mark.parametrize('edit', ['short', 'long'])
def test_adam7_exact_inflated_length_is_required(tmp_path, monkeypatch, colour, depth, edit):
    white = [[(x+2*y) % 3 == 0 for x in range(9)] for y in range(9)]
    raw = binary_rows(white, depth, colour, interlace=1)
    raw = raw[:-1] if edit == 'short' else raw+b'\0'
    palette = [(b'PLTE', b'\0\0\0\xff\xff\xff')] if colour == 3 else []
    rejection(tmp_path, raw_png(9, 9, depth, colour, raw, interlace=1, before=palette),
              'png_idat_length_mismatch', monkeypatch)


@pytest.mark.parametrize('semantics,reason', [(None, 'explicit_value_semantics_required'),
                                            ('probability', 'unsupported_mask_semantics')])
def test_png_semantics_errors_also_include_relative_path(tmp_path, semantics, reason):
    with pytest.raises(NumericContractError) as error:
        read_binary(tmp_path, 'nested/unread.png', synthetic=False, value_semantics=semantics)
    assert error.value.code == reason and 'nested/unread.png' in str(error.value)


def test_png_reads_bytes_once_with_requested_hash(tmp_path, monkeypatch):
    path = tmp_path/'once.png'
    data = raw_png(3, 2, 8, 0, b'\0\xff\0\0\0\0\xff\0')
    path.write_bytes(data)
    original = Path.read_bytes
    calls = []
    def count_reads(candidate):
        calls.append(candidate)
        return original(candidate)
    monkeypatch.setattr(Path, 'read_bytes', count_reads)
    assert read_binary(tmp_path, 'once.png', sha256=hashlib.sha256(data).hexdigest()).tolist() == WHITE
    assert calls == [path.resolve()]


def test_valid_png_at_exact_pixel_limit_preserves_pillow_globals(tmp_path, monkeypatch):
    from PIL import ImageFile
    path = tmp_path/'limit.png'; path.write_bytes(raw_png(3, 3, 8, 0, (b'\0\xff\0\xff')*3))
    monkeypatch.setattr(Image, 'MAX_IMAGE_PIXELS', 9)
    monkeypatch.setattr(ImageFile, 'LOAD_TRUNCATED_IMAGES', True)
    mask = read_binary(tmp_path, 'limit.png')
    assert mask.tolist() == [[True, False, True]]*3
    assert Image.MAX_IMAGE_PIXELS == 9 and ImageFile.LOAD_TRUNCATED_IMAGES is True
