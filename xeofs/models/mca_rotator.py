import numpy as np
from typing import Optional, Union, List, Tuple

from ._base_mca_rotator import _BaseMCARotator
from ._transformer import _MultiArrayTransformer
from ..utils.tools import squeeze

Array = np.ndarray
ArrayList = Union[Array, List[Array]]


class MCA_Rotator(_BaseMCARotator):
    '''Rotates a solution obtained from ``xe.models.MCA``.'''

    def __init__(
        self,
        n_rot : int,
        loadings : str = 'standard',
        power : int = 1,
        max_iter : int = 1000,
        rtol : float = 1e-8
    ):

        super().__init__(
            n_rot=n_rot, loadings=loadings, power=power,
            max_iter=max_iter, rtol=rtol
        )

    def singular_values(self) -> Array:
        return super().singular_values()

    def explained_covariance(self) -> Array:
        return super().explained_covariance()

    def squared_covariance_fraction(self) -> Array:
        return super().squared_covariance_fraction()

    def singular_vectors(self, scaling : int = 0) -> ArrayList:
        Vx, Vy = super().singular_vectors(scaling=scaling)
        Vx = self._model._tfx.back_transform_eofs(Vx)
        Vy = self._model._tfy.back_transform_eofs(Vy)
        return squeeze(Vx), squeeze(Vy)

    def pcs(self, scaling : int = 0) -> Array:
        Ux, Uy = super().pcs(scaling=scaling)
        Ux = self._model._tfx.back_transform_pcs(Ux)
        Uy = self._model._tfy.back_transform_pcs(Uy)
        return Ux, Uy

    def homogeneous_patterns(self) -> Tuple[ArrayList, ArrayList]:
        hom_pats, pvals = super().homogeneous_patterns()
        hom_pats_x = self._model._tfx.back_transform_eofs(hom_pats[0])
        hom_pats_y = self._model._tfy.back_transform_eofs(hom_pats[1])
        pvals_x = self._model._tfx.back_transform_eofs(pvals[0])
        pvals_y = self._model._tfy.back_transform_eofs(pvals[1])
        hom_pats_x = squeeze(hom_pats_x)
        hom_pats_y = squeeze(hom_pats_y)
        pvals_x = squeeze(pvals_x)
        pvals_y = squeeze(pvals_y)
        return (hom_pats_x, hom_pats_y), (pvals_x, pvals_y)

    def heterogeneous_patterns(self) -> Tuple[ArrayList, ArrayList]:
        het_pats, pvals = super().heterogeneous_patterns()
        het_pats_x = self._model._tfx.back_transform_eofs(het_pats[0])
        het_pats_y = self._model._tfy.back_transform_eofs(het_pats[1])
        pvals_x = self._model._tfx.back_transform_eofs(pvals[0])
        pvals_y = self._model._tfy.back_transform_eofs(pvals[1])
        het_pats_x = squeeze(het_pats_x)
        het_pats_y = squeeze(het_pats_y)
        pvals_x = squeeze(pvals_x)
        pvals_y = squeeze(pvals_y)
        return (het_pats_x, het_pats_y), (pvals_x, pvals_y)

    def reconstruct_XY(
        self,
        mode : Optional[Union[int, List[int], slice]] = None
    ) -> ArrayList:
        Xrec, Yrec = super().reconstruct_XY(mode=mode)
        Xrec = self._model._tfx.back_transform(Xrec)
        Yrec = self._model._tfy.back_transform(Yrec)
        return squeeze(Xrec), squeeze(Yrec)

    def project_onto_left_singular_vectors(
        self,
        X : ArrayList,
        scaling : int = 0
    ) -> Array:
        '''Project new data onto the rotated EOFs.

        Parameters
        ----------
        X : np.ndarray
             New data to project onto left singular vector. Data must have
             same feature shape as original data.
        scaling : [0, 1, 2]
            Projections are scaled (i) to be orthonormal (``scaling=0``), (ii) by the
            square root of the eigenvalues (``scaling=1``) or (iii) by the
            singular values (``scaling=2``) (the default is 0).

        '''
        proj = _MultiArrayTransformer()
        X = proj.fit_transform(X, axis=self._model._tfx.axis_samples)
        pcs = super().project_onto_left_singular_vectors(X=X, scaling=scaling)
        return proj.back_transform_pcs(pcs)

    def project_onto_right_singular_vectors(
        self,
        Y : ArrayList,
        scaling : int = 0
    ) -> Array:
        '''Project new data onto the rotated EOFs.

        Parameters
        ----------
        Y : np.ndarray
             New data to project onto right singular vector. Data must have
             same feature shape as original data.
        scaling : [0, 1, 2]
            Projections are scaled (i) to be orthonormal (``scaling=0``), (ii) by the
            square root of the eigenvalues (``scaling=1``) or (iii) by the
            singular values (``scaling=2``) (the default is 0).

        '''
        proj = _MultiArrayTransformer()
        Y = proj.fit_transform(Y, axis=self._model._tfy.axis_samples)
        pcs = super().project_onto_right_singular_vectors(Y=Y, scaling=scaling)
        return proj.back_transform_pcs(pcs)
