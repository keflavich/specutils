import logging
from copy import deepcopy

import numpy as np
from astropy import units as u
from astropy import constants as cnst
from astropy.nddata import NDDataRef
from astropy.utils.decorators import lazyproperty
from .spectrum_mixin import OneDSpectrumMixin
from .spectral_coordinate import SpectralCoord
from ..utils.wcs_utils import gwcs_from_array

__all__ = ['Spectrum1D']

__doctest_skip__ = ['Spectrum1D.spectral_resolution']


class Spectrum1D(OneDSpectrumMixin, NDDataRef):
    """
    Spectrum container for 1D spectral data.

    Parameters
    ----------
    flux : `astropy.units.Quantity` or astropy.nddata.NDData`-like
        The flux data for this spectrum.
    spectral_axis : `astropy.units.Quantity`
        Dispersion information with the same shape as the last (or only)
        dimension of flux.
    wcs : `astropy.wcs.WCS` or `gwcs.wcs.WCS`
        WCS information object.
    velocity_convention : {"doppler_relativistic", "doppler_optical", "doppler_radio"}
        Convention used for velocity conversions.
    rest_value : `~astropy.units.Quantity`
        Any quantity supported by the standard spectral equivalencies
        (wavelength, energy, frequency, wave number). Describes the rest value
        of the spectral axis for use with velocity conversions.
    redshift
        See `redshift` for more information.
    radial_velocity
        See `radial_velocity` for more information.
    uncertainty : `~astropy.nddata.NDUncertainty`
        Contains uncertainty information along with propagation rules for
        spectrum arithmetic. Can take a unit, but if none is given, will use
        the unit defined in the flux.
    meta : dict
        Arbitrary container for any user-specific information to be carried
        around with the spectrum container object.
    """
    def __init__(self, flux=None, spectral_axis=None, wcs=None,
                 velocity_convention=None, rest_value=None, redshift=None,
                 radial_velocity=None, **kwargs):
        # Check for pre-defined entries in the kwargs dictionary.
        unknown_kwargs = set(kwargs).difference(
            {'data', 'unit', 'uncertainty', 'meta', 'mask', 'copy'})

        if len(unknown_kwargs) > 0:
            raise ValueError("Initializer contains unknown arguments(s): {}."
                             "".format(', '.join(map(str, unknown_kwargs))))

        # If the flux (data) argument is a subclass of nddataref (as it would
        # be for internal arithmetic operations), avoid setup entirely.
        if isinstance(flux, NDDataRef):
            self._velocity_convention = flux._velocity_convention
            self._rest_value = flux._rest_value

            super(Spectrum1D, self).__init__(flux)
            return

        # Ensure that the flux argument is an astropy quantity
        if flux is not None:
            if not isinstance(flux, u.Quantity):
                raise ValueError("Flux must be a `Quantity` object.")
            elif flux.isscalar:
                flux = u.Quantity([flux])

        # Ensure that only one or neither of these parameters is set
        if redshift is not None and radial_velocity is not None:
            raise ValueError('cannot set both radial_velocity and redshift at '
                             'the same time.')

        # In cases of slicing, new objects will be initialized with `data`
        # instead of ``flux``. Ensure we grab the `data` argument.
        if flux is None and 'data' in kwargs:
            flux = kwargs.pop('data')

        # Ensure that the unit information codified in the quantity object is
        # the One True Unit.
        kwargs.setdefault('unit', flux.unit if isinstance(flux, u.Quantity)
                                            else kwargs.get('unit'))

        # In the case where the arithmetic operation is being performed with
        # a single float, int, or array object, just go ahead and ignore wcs
        # requirements
        if (not isinstance(flux, u.Quantity) or isinstance(flux, float)
            or isinstance(flux, int)) and np.ndim(flux) == 0:

            super(Spectrum1D, self).__init__(data=flux, wcs=wcs, **kwargs)
            return

        # Attempt to parse the spectral axis. If none is given, try instead to
        # parse a given wcs. This is put into a GWCS object to
        # then be used behind-the-scenes for all specutils operations.
        if spectral_axis is not None:
            # Ensure that the spectral axis is an astropy Quantity
            if not isinstance(spectral_axis, u.Quantity):
                raise ValueError("Spectral axis must be a `Quantity` or `SpectralCoord` object.")

            # If spectral axis is provided as an astropy Quantity, convert it
            # to a specutils SpectralCoord object
            if not isinstance(spectral_axis, SpectralCoord):
                self.spectral_axis = SpectralCoord(spectral_axis, redshift=redshift,
                        radial_velocity=radial_velocity, doppler_rest=rest_value,
                        doppler_convention=velocity_convention)

            wcs = gwcs_from_array(spectral_axis)
        elif wcs is None:
            # If no spectral axis or wcs information is provided, initialize
            # with an empty gwcs based on the flux.
            size = len(flux) if not flux.isscalar else 1
            wcs = gwcs_from_array(np.arange(size) * u.Unit(""))

        # Check to make sure the wavelength length is the same in both
        if flux is not None and spectral_axis is not None:
            if not spectral_axis.shape[0] == flux.shape[-1]:
                raise ValueError('Spectral axis ({}) and the last flux axis ({}) lengths must be the same'.format(
                    spectral_axis.shape[0], flux.shape[-1]))

        self._velocity_convention = velocity_convention

        if rest_value is None:
            if hasattr(wcs, 'rest_frequency') and wcs.rest_frequency != 0:
                self._rest_value = wcs.rest_frequency * u.Hz
            elif hasattr(wcs, 'rest_wavelength') and wcs.rest_wavelength != 0:
                self._rest_value = wcs.rest_wavelength * u.AA
            else:
                self._rest_value = 0 * u.AA
        else:
            self._rest_value = rest_value

            if not isinstance(self._rest_value, u.Quantity):
                logging.info("No unit information provided with rest value. "
                             "Assuming units of spectral axis ('%s').",
                             spectral_axis.unit)
                self._rest_value = u.Quantity(rest_value, spectral_axis.unit)
            elif not self._rest_value.unit.is_equivalent(u.AA) \
                    and not self._rest_value.unit.is_equivalent(u.Hz):
                raise u.UnitsError("Rest value must be "
                                   "energy/wavelength/frequency equivalent.")

        super(Spectrum1D, self).__init__(
            data=flux.value if isinstance(flux, u.Quantity) else flux,
            wcs=wcs, **kwargs)

        # set redshift after super() - necessary because the shape-checking
        # requires that the flux be initialized
        self.radial_velocity = self.spectral_axis.radial_velocity

        if hasattr(self, 'uncertainty') and self.uncertainty is not None:
            if not flux.shape == self.uncertainty.array.shape:
                raise ValueError('Flux axis ({}) and uncertainty ({}) shapes must be the same.'.format(
                    flux.shape, self.uncertainty.array.shape))

    def __getitem__(self, item):
        """
        Override the class indexer. We do this here because there are two cases
        for slicing on a ``Spectrum1D``:

            1.) When the flux is one dimensional, indexing represents a single
                flux value at a particular spectral axis bin, and returns a new
                ``Spectrum1D`` where all attributes are sliced.
            2.) When flux is multi-dimensional (i.e. several fluxes over the
                same spectral axis), indexing returns a new ``Spectrum1D`` with
                the sliced flux range and a deep copy of all other attributes.

        The first case is handled by the parent class, while the second is
        handled here.
        """
        if len(self.flux.shape) > 1:
            return self._copy(
                flux=self.flux[item], uncertainty=self.uncertainty[item]
                    if self.uncertainty is not None else None)

        if not isinstance(item, slice):
            item = slice(item, item+1, None)

        return super().__getitem__(item)

    def _copy(self, **kwargs):
        """
        Peform deep copy operations on each attribute of the ``Spectrum1D``
        object.
        """
        alt_kwargs = dict(
            flux=deepcopy(self.flux),
            spectral_axis=deepcopy(self.spectral_axis),
            uncertainty=deepcopy(self.uncertainty),
            wcs=deepcopy(self.wcs),
            mask=deepcopy(self.mask),
            meta=deepcopy(self.meta),
            unit=deepcopy(self.unit),
            velocity_convention=deepcopy(self.velocity_convention),
            rest_value=deepcopy(self.rest_value))

        alt_kwargs.update(kwargs)

        return self.__class__(**alt_kwargs)

    @property
    def frequency(self):
        """
        The frequency as a `~astropy.units.Quantity` in units of GHz
        """
        return self.spectral_axis.to(u.GHz, u.spectral())

    @property
    def wavelength(self):
        """
        The wavelength as a `~astropy.units.Quantity` in units of Angstroms
        """
        return self.spectral_axis.to(u.AA, u.spectral())

    @property
    def energy(self):
        """
        The energy of the spectral axis as a `~astropy.units.Quantity` in units
        of eV.
        """
        return self.spectral_axis.to(u.eV, u.spectral())

    @property
    def photon_flux(self):
        """
        The flux density of photons as a `~astropy.units.Quantity`, in units of
        photons per cm^2 per second per spectral_axis unit
        """
        flux_in_spectral_axis_units = self.flux.to(
            u.W * u.cm**-2 * self.spectral_axis.unit**-1,
            u.spectral_density(self.spectral_axis))
        photon_flux_density = flux_in_spectral_axis_units / (self.energy / u.photon)

        return photon_flux_density.to(u.photon * u.cm**-2 * u.s**-1 *
                                      self.spectral_axis.unit**-1)

    @lazyproperty
    def bin_edges(self):
        return self.wcs.bin_edges()

    @property
    def shape(self):
        return self.flux.shape

    @property
    def redshift(self):
        """
        The redshift(s) of the objects represented by this spectrum.  May be
        scalar (if this spectrum's ``flux`` is 1D) or vector.  Note that
        the concept of "redshift of a spectrum" can be ambiguous, so the
        interpretation is set to some extent by either the user, or operations
        (like template fitting) that set this attribute when they are run on
        a spectrum.
        """
        return self._radial_velocity/cnst.c

    @redshift.setter
    def redshift(self, val):
        if val is None:
            self._radial_velocity = None
        else:
            self.radial_velocity = val * cnst.c

    @property
    def radial_velocity(self):
        """
        The radial velocity(s) of the objects represented by this spectrum.  May
        be scalar (if this spectrum's ``flux`` is 1D) or vector.  Note that
        the concept of "RV of a spectrum" can be ambiguous, so the
        interpretation is set to some extent by either the user, or operations
        (like template fitting) that set this attribute when they are run on
        a spectrum.
        """
        return self._radial_velocity

    @radial_velocity.setter
    def radial_velocity(self, val):
        if val is None:
            self._radial_velocity = None
        else:
            if not val.unit.is_equivalent(u.km/u.s):
                raise u.UnitsError('radial_velocity must be a velocity')

            # the trick below checks if the two shapes given are broadcastable onto
            # each other. See https://stackoverflow.com/questions/47243451/checking-if-two-arrays-are-broadcastable-in-python
            input_shape = val.shape
            flux_shape = self.flux.shape[:-1]
            if not all((m == n) or (m == 1) or (n == 1)
                   for m, n in zip(input_shape[::-1], flux_shape)):
                raise ValueError("radial_velocity or redshift must have shape that "
                                 "is compatible with this spectrum's flux array")
            self._radial_velocity = val

    def __add__(self, other):
        if not isinstance(other, NDDataRef):
            other = u.Quantity(other, unit=self.unit)

        return self.add(other)

    def __sub__(self, other):
        if not isinstance(other, NDDataRef):
            other = u.Quantity(other, unit=self.unit)

        return self.subtract(other)

    def __mul__(self, other):
        if not isinstance(other, NDDataRef):
            other = u.Quantity(other)

        return self.multiply(other)

    def __div__(self, other):
        if not isinstance(other, NDDataRef):
            other = u.Quantity(other)

        return self.divide(other)

    def __truediv__(self, other):
        if not isinstance(other, NDDataRef):
            other = u.Quantity(other)

        return self.divide(other)

    def _format_array_summary(self, label, array):
        if len(array) == 1:
            mean = np.mean(array)
            s = "{:17} [ {:.5} ],  mean={:.5}"
            return s.format(label+':', array[0], array[-1], mean)
        elif len(array) > 1:
            mean = np.mean(array)
            s = "{:17} [ {:.5}, ..., {:.5} ],  mean={:.5}"
            return s.format(label+':', array[0], array[-1], mean)
        else:
            return "{:17} [ ],  mean= n/a".format(label+':')

    def __str__(self):
        result = "Spectrum1D "
        # Handle case of single value flux
        if self.flux.ndim == 0:
            result += "(length=1)\n"
            return result + "flux:   {}".format(self.flux)

        # Handle case of multiple flux arrays
        result += "(length={})\n".format(len(self.spectral_axis))
        if self.flux.ndim > 1:
            for i, flux in enumerate(self.flux):
                label = 'flux{:2}'.format(i)
                result += self._format_array_summary(label, flux) + '\n'
        else:
            result += self._format_array_summary('flux', self.flux) + '\n'
        # Add information about spectral axis
        result += self._format_array_summary('spectral axis', self.spectral_axis)
        # Add information about uncertainties if available
        if self.uncertainty:
            result += "\nuncertainty:      [ {}, ..., {} ]".format(
                self.uncertainty[0], self.uncertainty[-1])
        return result

    def __repr__(self):
        inner_str = "flux={}, spectral_axis={}".format(repr(self.flux),
                                                       repr(self.spectral_axis))

        if self.uncertainty is not None:
            inner_str += ", uncertainty={}".format(repr(self.uncertainty))

        result = "<Spectrum1D({})>".format(inner_str)

        return result
