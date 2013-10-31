# This module provides a basic and probably temporary WCS solution until astropy has a wcs built-in

import numpy as np

from astropy.utils import misc
from astropy.io import fits
from astropy.modeling import Model
from astropy.modeling.parameters import Parameter

import astropy.units as u

valid_spectral_units = [u.pix, u.km/u.s, u.m, u.Hz, u.erg]



def check_valid_unit(unit):
    if not any([unit.is_equivalent(x) for x in valid_spectral_units]):
                raise ValueError("Unit %r is not recognized as a valid spectral unit.  Valid units are: " % unit.to_string() +
                                 ", ".join([x.to_string() for x in valid_spectral_units]))

class Spectrum1DWCSError(Exception):
    pass


class Spectrum1DWCSFITSError(Spectrum1DWCSError):
    pass

class Spectrum1DWCSUnitError(Spectrum1DWCSError):
    pass

class BaseSpectrum1DWCS(Model):
    """
    Base class for a Spectrum1D WCS
    """


    n_inputs = 1
    n_outputs = 1



class Spectrum1DLookupWCS(BaseSpectrum1DWCS):
    """
    A simple lookup table wcs

    Parameters
    ----------

    lookup_table : ~np.ndarray or ~astropy.units.Quantity
        lookup table for the array
    """

    n_inputs = 1
    n_outputs = 1
    lookup_table_parameter = Parameter('lookup_table_parameter')

    def __init__(self, lookup_table, unit=None, lookup_table_interpolation_kind='linear'):
        super(Spectrum1DLookupWCS, self).__init__()
        if unit is None and not hasattr(lookup_table, 'unit'):
            raise TypeError("Lookup table must have a unit attribute or units must be specified.")
        elif unit is not None:
            try:
                unit = u.Unit(unit)
            except ValueError:
                # e.g., Unit("blah")
                raise ValueError("Invalid unit specified")

            check_valid_unit(unit)

            lookup_table *= unit



        self.lookup_table_parameter = lookup_table
        ##### Quick fix - needs to be fixed in modelling ###
        self.unit = lookup_table.unit

        self.lookup_table_interpolation_kind = lookup_table_interpolation_kind

        #Making sure that 1d transformations are sensible
        assert self.lookup_table_parameter.value.ndim == 1

        #check that array gives a bijective transformation (that forwards and backwards transformations are unique)
        if len(self.lookup_table_parameter.value) != len(np.unique(self.lookup_table_parameter.value)):
            raise Spectrum1DWCSError('The Lookup Table does not describe a unique transformation')
        self.pixel_index = np.arange(len(self.lookup_table_parameter.value))

    def __call__(self, pixel_indices):
        if self.lookup_table_interpolation_kind == 'linear':
            return np.interp(pixel_indices, self.pixel_index, self.lookup_table_parameter.value, left=np.nan,
                             right=np.nan) * self.unit
        else:
            raise NotImplementedError('Interpolation type %s is not implemented' % self.lookup_table_interpolation_kind)


    def invert(self, dispersion_values):
        if self.lookup_table_interpolation_kind == 'linear':
            return np.interp(dispersion_values, self.lookup_table_parameter.value, self.pixel_index, left=np.nan, right=np.nan)
        else:
            raise NotImplementedError('Interpolation type %s is not implemented' % self.lookup_table_interpolation_kind)



class Spectrum1DLinearWCS(BaseSpectrum1DWCS):
    """
    A simple linear wcs
    """

    dispersion0 = Parameter('dispersion0')
    dispersion_delta = Parameter('dispersion_delta')



    @classmethod
    def from_fits_header(cls, header, unit=None, spectroscopic_axis_number=1,
            **kwargs):
        """
        Load a simple linear Spectral WCS from a FITS header.

        Respects the following keywords:

         * CDELT or CD: delta-dispersion
         * CRVAL: reference position
         * CRPIX: reference pixel
         * CUNIT: dispersion units (parsed by astropy.units)

        Parameters
        ----------
        header : str
            FITS Filename or FITS Header instance
        unit : astropy.units.Unit
            Specified units. Overrides CUNIT1 if specified.

        .. todo:: Allow FITS files or just headers to be passed...
        """
        if isinstance(header, basestring):
            header = fits.getheader(header, **kwargs)

        if unit is None:
            try:
                unit = u.Unit(header.get('CUNIT%i' % spectroscopic_axis_number))
            # UnitsException is never really called, do we need to put it here?
            except (u.UnitsException, TypeError):
                raise Spectrum1DWCSUnitError("No units were specified and CUNIT did not contain unit information.")


        try:
            cdelt = header['CDELT%i' % spectroscopic_axis_number]
        except KeyError:
            try:
                cdelt = header['CD%i_%i' % (spectroscopic_axis_number,spectroscopic_axis_number)]
            except KeyError:
                raise Spectrum1DWCSFITSError('Necessary keywords (CDELT or CD) missing - can not reconstruct WCS')
        try:
            crpix = header['CRPIX%i' % spectroscopic_axis_number]
            crval = header['CRVAL%i' % spectroscopic_axis_number]
        except KeyError:
            raise Spectrum1DWCSFITSError('Necessary keywords (CRPIX, CRVAL) missing - can not reconstruct WCS')

        # What happens if both of them are present (fix???)
        #if cdelt is None:
        #    cdelt = header.get('CD%i_%i' % (spectroscopic_axis_number,spectroscopic_axis_number))


        return cls(crval, cdelt, crpix - 1, unit=unit)


    def __init__(self, dispersion0, dispersion_delta, pixel_index, unit=None):
        super(Spectrum1DLinearWCS, self).__init__()

        #### Not clear what to do about units of dispersion0 and dispersion_delta.
        # dispersion0 should have units like angstrom, whereas dispersion_delta should have units like angstrom/pix
        # for now I assume pixels don't have units and both dispersion0 and dispersion_delta should have the same unit
        dispersion0 = u.Quantity(dispersion0, unit)
        dispersion_delta = u.Quantity(dispersion_delta, unit)

        check_valid_unit(dispersion0.unit)
        check_valid_unit(dispersion_delta.unit)


        ##### Quick fix - needs to be fixed in modelling ###
        if unit is None:
            unit = dispersion0.unit

        self.unit = unit

        self.dispersion0 = dispersion0.value
        self.dispersion_delta = dispersion_delta.value
        self.pixel_index = pixel_index



    def __call__(self, pixel_indices):
        if misc.isiterable(pixel_indices) and not isinstance(pixel_indices, basestring):
            pixel_indices = np.array(pixel_indices)
        return (self.dispersion0 + self.dispersion_delta * (pixel_indices - self.pixel_index)) * self.unit

    def invert(self, dispersion_values):
        if not hasattr(dispersion_values, 'unit'):
            raise u.UnitsException('Must give a dispersion value with a valid unit (i.e. quantity 5 * u.Angstrom)')

        if misc.isiterable(dispersion_values) and not isinstance(dispersion_values, basestring):
            dispersion_values = np.array(dispersion_values)
        return float((dispersion_values - self.dispersion0) / self.dispersion_delta) + self.pixel_index

    def to_fits_header(self, fits_header, spectroscopic_axis_number=1):

        fits_header['crval%d' % spectroscopic_axis_number] = self.dispersion0.value
        fits_header['crpix%d' % spectroscopic_axis_number] = self.pixel_index + 1
        fits_header['cdelt%d' % spectroscopic_axis_number] = self.dispersion_delta.value
        fits_header['cunit%d' % spectroscopic_axis_number] = self.unit.to_string()


#### EXAMPLE implementation for Chebyshev
#class ChebyshevSpectrum1D(models.ChebyshevModel):
class ChebyshevSpectrum1D(BaseSpectrum1DWCS):

    @classmethod
    def from_fits_header(cls, header):
        pass
        ### here be @hamogu's code ###
        #degree, parameters = hamogu_read_fits(header)
        #return cls(degree, **parameters)



#def fits_wcs_reader




fits_capable_wcs = [Spectrum1DLinearWCS]

