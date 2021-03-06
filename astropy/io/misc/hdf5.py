# Licensed under a 3-clause BSD style license - see LICENSE.rst
"""
This package contains functions for reading and writing HDF5 tables that are
not meant to be used directly, but instead are available as readers/writers in
`astropy.table`. See :ref:`table_io` for more details.
"""

import os
import warnings

import numpy as np

# NOTE: Do not import anything from astropy.table here.
# https://github.com/astropy/astropy/issues/6604
from ...utils.exceptions import AstropyUserWarning, AstropyDeprecationWarning

HDF5_SIGNATURE = b'\x89HDF\r\n\x1a\n'
META_KEY = '__table_column_meta__'

__all__ = ['read_table_hdf5', 'write_table_hdf5']


def meta_path(path):
    return path + '.' + META_KEY


def _find_all_structured_arrays(handle):
    """
    Find all structured arrays in an HDF5 file
    """
    import h5py
    structured_arrays = []

    def append_structured_arrays(name, obj):
        if isinstance(obj, h5py.Dataset) and obj.dtype.kind == 'V':
            structured_arrays.append(name)
    handle.visititems(append_structured_arrays)
    return structured_arrays


def is_hdf5(origin, filepath, fileobj, *args, **kwargs):

    if fileobj is not None:
        loc = fileobj.tell()
        try:
            signature = fileobj.read(8)
        finally:
            fileobj.seek(loc)
        return signature == HDF5_SIGNATURE
    elif filepath is not None:
        return filepath.endswith(('.hdf5', '.h5'))

    try:
        import h5py
    except ImportError:
        return False
    else:
        return isinstance(args[0], (h5py.highlevel.File, h5py.highlevel.Group, h5py.highlevel.Dataset))


def read_table_hdf5(input, path=None):
    """
    Read a Table object from an HDF5 file

    This requires `h5py <http://www.h5py.org/>`_ to be installed. If more than one
    table is present in the HDF5 file or group, the first table is read in and
    a warning is displayed.

    Parameters
    ----------
    input : str or :class:`h5py:File` or :class:`h5py:Group` or
        :class:`h5py:Dataset` If a string, the filename to read the table from.
        If an h5py object, either the file or the group object to read the
        table from.
    path : str
        The path from which to read the table inside the HDF5 file.
        This should be relative to the input file or group.
    """

    try:
        import h5py
    except ImportError:
        raise Exception("h5py is required to read and write HDF5 files")

    # This function is iterative, and only gets to writing the file when
    # the input is an hdf5 Group. Moreover, the input variable is changed in
    # place.
    # Here, we save its value to be used at the end when the conditions are
    # right.
    input_save = input
    if isinstance(input, (h5py.highlevel.File, h5py.highlevel.Group)):

        # If a path was specified, follow the path

        if path is not None:
            try:
                input = input[path]
            except (KeyError, ValueError):
                raise OSError("Path {0} does not exist".format(path))

        # `input` is now either a group or a dataset. If it is a group, we
        # will search for all structured arrays inside the group, and if there
        # is one we can proceed otherwise an error is raised. If it is a
        # dataset, we just proceed with the reading.

        if isinstance(input, h5py.highlevel.Group):

            # Find all structured arrays in group
            arrays = _find_all_structured_arrays(input)

            if len(arrays) == 0:
                raise ValueError("no table found in HDF5 group {0}".
                                 format(path))
            elif len(arrays) > 0:
                path = arrays[0] if path is None else path + '/' + arrays[0]
                warnings.warn("path= was not specified but multiple tables"
                              " are present, reading in first available"
                              " table (path={0})".format(path),
                              AstropyUserWarning)
                return read_table_hdf5(input, path=path)

    elif not isinstance(input, h5py.highlevel.Dataset):

        # If a file object was passed, then we need to extract the filename
        # because h5py cannot properly read in file objects.

        if hasattr(input, 'read'):
            try:
                input = input.name
            except AttributeError:
                raise TypeError("h5py can only open regular files")

        # Open the file for reading, and recursively call read_table_hdf5 with
        # the file object and the path.

        f = h5py.File(input, 'r')

        try:
            return read_table_hdf5(f, path=path)
        finally:
            f.close()

    # If we are here, `input` should be a Dataset object, which we can now
    # convert to a Table.

    # Create a Table object
    from ...table import Table, meta

    table = Table(np.array(input))

    # Read the meta-data from the file. For back-compatibility, we can read
    # the old file format where the serialized metadata were saved in the
    # attributes of the HDF5 dataset.
    # In the new format, instead, metadata are stored in a new dataset in the
    # same file. This is introduced in Astropy 3.0
    old_version_meta = META_KEY in input.attrs
    new_version_meta = path is not None and meta_path(path) in input_save
    if old_version_meta or new_version_meta:
        if new_version_meta:
            header = meta.get_header_from_yaml(
                h.decode('utf-8') for h in input_save[meta_path(path)])
        elif old_version_meta:
            header = meta.get_header_from_yaml(
                h.decode('utf-8') for h in input.attrs[META_KEY])
        if 'meta' in list(header.keys()):
            table.meta = header['meta']

        header_cols = dict((x['name'], x) for x in header['datatype'])
        for col in table.columns.values():
            for attr in ('description', 'format', 'unit', 'meta'):
                if attr in header_cols[col.name]:
                    setattr(col, attr, header_cols[col.name][attr])
    else:
        # Read the meta-data from the file
        table.meta.update(input.attrs)

    return table


def write_table_hdf5(table, output, path=None, compression=False,
                     append=False, overwrite=False, serialize_meta=False,
                     compatibility_mode=False):
    """
    Write a Table object to an HDF5 file

    This requires `h5py <http://www.h5py.org/>`_ to be installed.

    Parameters
    ----------
    table : `~astropy.table.Table`
        Data table that is to be written to file.
    output : str or :class:`h5py:File` or :class:`h5py:Group`
        If a string, the filename to write the table to. If an h5py object,
        either the file or the group object to write the table to.
    path : str
        The path to which to write the table inside the HDF5 file.
        This should be relative to the input file or group.
    compression : bool or str or int
        Whether to compress the table inside the HDF5 file. If set to `True`,
        ``'gzip'`` compression is used. If a string is specified, it should be
        one of ``'gzip'``, ``'szip'``, or ``'lzf'``. If an integer is
        specified (in the range 0-9), ``'gzip'`` compression is used, and the
        integer denotes the compression level.
    append : bool
        Whether to append the table to an existing HDF5 file.
    overwrite : bool
        Whether to overwrite any existing file without warning.
        If ``append=True`` and ``overwrite=True`` then only the dataset will be
        replaced; the file/group will not be overwritten.
    """
    from ...table import meta

    try:
        import h5py
    except ImportError:
        raise Exception("h5py is required to read and write HDF5 files")

    # Tables with mixin columns are not supported
    if table.has_mixin_columns:
        mixin_names = [name for name, col in table.columns.items()
                       if not isinstance(col, table.ColumnClass)]
        raise ValueError('cannot write table with mixin column(s) {0} to HDF5'
                         .format(mixin_names))

    if path is None:
        raise ValueError("table path should be set via the path= argument")
    elif path.endswith('/'):
        raise ValueError("table path should end with table name, not /")

    if '/' in path:
        group, name = path.rsplit('/', 1)
    else:
        group, name = None, path

    if isinstance(output, (h5py.highlevel.File, h5py.highlevel.Group)):

        if group:
            try:
                output_group = output[group]
            except (KeyError, ValueError):
                output_group = output.create_group(group)
        else:
            output_group = output

    elif isinstance(output, str):

        if os.path.exists(output) and not append:
            if overwrite and not append:
                os.remove(output)
            else:
                raise OSError("File exists: {0}".format(output))

        # Open the file for appending or writing
        f = h5py.File(output, 'a' if append else 'w')

        # Recursively call the write function
        try:
            return write_table_hdf5(table, f, path=path,
                                    compression=compression, append=append,
                                    overwrite=overwrite,
                                    serialize_meta=serialize_meta,
                                    compatibility_mode=compatibility_mode)
        finally:
            f.close()

    else:

        raise TypeError('output should be a string or an h5py File or '
                        'Group object')

    # Check whether table already exists
    if name in output_group:
        if append and overwrite:
            # Delete only the dataset itself
            del output_group[name]
        else:
            raise OSError("Table {0} already exists".format(path))

    # Write the table to the file
    if compression:
        if compression is True:
            compression = 'gzip'
        dset = output_group.create_dataset(name, data=table.as_array(),
                                           compression=compression)
    else:
        dset = output_group.create_dataset(name, data=table.as_array())

    if serialize_meta:
        header_yaml = meta.get_yaml_from_table(table)

        header_encoded = [h.encode('utf-8') for h in header_yaml]
        if compatibility_mode:
            warnings.warn("compatibility mode for writing is deprecated",
                          AstropyDeprecationWarning)
            try:
                dset.attrs[META_KEY] = header_encoded
            except Exception as e:
                warnings.warn(
                "Attributes could not be written to the output HDF5 "
                "file: {0}".format(e))

        else:
            output_group.create_dataset(meta_path(name),
                                        data=header_encoded)

    else:
        # Write the meta-data to the file
        for key in table.meta:
            val = table.meta[key]
            try:
                dset.attrs[key] = val
            except TypeError:
                warnings.warn("Attribute `{0}` of type {1} cannot be written to "
                              "HDF5 files - skipping. (Consider specifying "
                              "serialize_meta=True to write all meta data)".format(key, type(val)),
                              AstropyUserWarning)


def register_hdf5():
    """
    Register HDF5 with Unified I/O.
    """
    from .. import registry as io_registry
    from ...table import Table

    io_registry.register_reader('hdf5', Table, read_table_hdf5)
    io_registry.register_writer('hdf5', Table, write_table_hdf5)
    io_registry.register_identifier('hdf5', Table, is_hdf5)
