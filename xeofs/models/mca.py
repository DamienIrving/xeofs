from typing import Tuple

import numpy as np
import xarray as xr
from dask.diagnostics.progress import ProgressBar

from ._base_cross_model import _BaseCrossModel
from .decomposer import CrossDecomposer
from ..utils.data_types import AnyDataObject, DataArray
from ..data_container.mca_data_container import MCADataContainer, ComplexMCADataContainer
from ..utils.statistics import pearson_correlation
from ..utils.xarray_utils import hilbert_transform


class MCA(_BaseCrossModel):
    '''Maximum Covariance Analyis (MCA).
    
    Parameters:
    -------------
    n_modes: int, default=10
        Number of modes to calculate.
    standardize: bool, default=False
        Whether to standardize the input data.
    use_coslat: bool, default=False
        Whether to use cosine of latitude for scaling.
    use_weights: bool, default=False
        Whether to use additional weights.

    '''

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.attrs.update({'model': 'MCA'})


    def fit(self, data1: AnyDataObject, data2: AnyDataObject, dim, weights1=None, weights2=None):
        '''
        Fit the model.

        Parameters:
        -------------
        data1: xr.DataArray or list of xarray.DataArray
            Left input data.
        data2: xr.DataArray or list of xarray.DataArray
            Right input data.
        dim: tuple
            Tuple specifying the sample dimensions. The remaining dimensions 
            will be treated as feature dimensions.
        weights1: xr.DataArray or xr.Dataset or None, default=None
            If specified, the left input data will be weighted by this array.
        weights2: xr.DataArray or xr.Dataset or None, default=None
            If specified, the right input data will be weighted by this array.

        '''
        data1_processed: DataArray = self.preprocessor1.fit_transform(data1, dim, weights1)
        data2_processed: DataArray = self.preprocessor2.fit_transform(data2, dim, weights2)

        decomposer = CrossDecomposer(n_modes=self._params['n_modes'])
        decomposer.fit(data1_processed, data2_processed)

        # Note:
        # - explained variance is given by the singular values of the SVD;
        # - We use the term singular_values_pca as used in the context of PCA:
        # Considering data X1 = X2, MCA is the same as PCA. In this case,
        # singular_values_pca is equivalent to the singular values obtained
        # when performing PCA of X1 or X2.
        singular_values = decomposer.singular_values_
        squared_covariance = singular_values**2
        total_squared_covariance = decomposer.total_squared_covariance_
        # singular_values_pca = np.sqrt(singular_values * (data1.sample.size - 1))
        singular_vectors1 = decomposer.singular_vectors1_
        singular_vectors2 = decomposer.singular_vectors2_
        norm1 = np.sqrt(singular_values)
        norm2 = np.sqrt(singular_values)

        # Index of the sorted squared covariance
        idx_sorted_modes = squared_covariance.compute().argsort()[::-1]
        idx_sorted_modes.coords.update(squared_covariance.coords)

        # Project the data onto the singular vectors
        scores1 = xr.dot(data1_processed, singular_vectors1, dims='feature') / norm1
        scores2 = xr.dot(data2_processed, singular_vectors2, dims='feature') / norm2

        self.data = MCADataContainer(
            input_data1=data1_processed,
            input_data2=data2_processed,
            components1=singular_vectors1,
            components2=singular_vectors2,
            scores1=scores1,
            scores2=scores2,
            squared_covariance=squared_covariance,
            total_squared_covariance=total_squared_covariance,
            idx_modes_sorted=idx_sorted_modes,
            norm1=norm1,
            norm2=norm2,
        )
        # Assign analysis-relevant meta data
        self.data.set_attrs(self.attrs)

    def transform(self, **kwargs):
        '''Project new unseen data onto the singular vectors.

        Parameters:
        -------------
        data1: xr.DataArray or list of xarray.DataArray
            Left input data. Must be provided if `data2` is not provided.
        data2: xr.DataArray or list of xarray.DataArray
            Right input data. Must be provided if `data1` is not provided.

        Returns:
        ----------
        scores1: DataArray | Dataset | List[DataArray]
            Left scores.
        scores2: DataArray | Dataset | List[DataArray]
            Right scores.

        '''
        results = []
        if 'data1' in kwargs.keys():
            # Preprocess input data
            data1 = kwargs['data1']
            data1 = self.preprocessor1.transform(data1)
            # Project data onto singular vectors
            comps1 = self.data.components1
            norm1 = self.data.norm1
            scores1 = xr.dot(data1, comps1) / norm1
            # Inverse transform scores
            scores1 = self.preprocessor1.inverse_transform_scores(scores1)
            results.append(scores1)

        if 'data2' in kwargs.keys():
            # Preprocess input data
            data2 = kwargs['data2']
            data2 = self.preprocessor2.transform(data2)
            # Project data onto singular vectors
            comps2 = self.data.components2
            norm2 = self.data.norm2
            scores2 = xr.dot(data2, comps2) / norm2
            # Inverse transform scores
            scores2 = self.preprocessor2.inverse_transform_scores(scores2)
            results.append(scores2)

        return results

    def inverse_transform(self, mode):
        '''Reconstruct the original data from transformed data.

        Parameters:
        -------------
        mode: scalars, slices or array of tick labels.
            The mode(s) used to reconstruct the data. If a scalar is given,
            the data will be reconstructed using the given mode. If a slice
            is given, the data will be reconstructed using the modes in the
            given slice. If a array is given, the data will be reconstructed
            using the modes in the given array.

        Returns:
        ----------
        Xrec1: DataArray | Dataset | List[DataArray]
            Reconstructed data of left field.
        Xrec2: DataArray | Dataset | List[DataArray]
            Reconstructed data of right field.

        '''
        # Singular vectors
        comps1 = self.data.components1.sel(mode=mode)
        comps2 = self.data.components2.sel(mode=mode)

        # Scores = projections
        scores1 = self.data.scores1.sel(mode=mode)
        scores2 = self.data.scores2.sel(mode=mode)

        # Norms
        norm1 = self.data.norm1.sel(mode=mode)
        norm2 = self.data.norm2.sel(mode=mode)

        # Reconstruct the data
        data1 = xr.dot(scores1, comps1.conj() * norm1, dims='mode')
        data2 = xr.dot(scores2, comps2.conj() * norm2, dims='mode')

        # Enforce real output
        data1 = data1.real
        data2 = data2.real
        
        # Unstack and rescale the data
        data1 = self.preprocessor1.inverse_transform_data(data1)
        data2 = self.preprocessor2.inverse_transform_data(data2)

        return data1, data2

    def squared_covariance(self):
        '''Get the squared covariance.

        The squared covariance corresponds to the explained variance in PCA and is given by the 
        squared singular values of the covariance matrix.
            
        '''
        return self.data.squared_covariance
    
    def squared_covariance_fraction(self):
        '''Calculate the squared covariance fraction (SCF).

        The SCF is a measure of the proportion of the total squared covariance that is explained by each mode `i`. It is computed 
        as follows:

        .. math::
        SCF_i = \\frac{\\sigma_i^2}{\\sum_{i=1}^{m} \\sigma_i^2}

        where `m` is the total number of modes and :math:`\\sigma_i` is the `ith` singular value of the covariance matrix.

        '''
        return self.data.squared_covariance_fraction
    
    def components(self):
        '''Return the singular vectors of the left and right field.
        
        Returns:
        ----------
        components1: DataArray | Dataset | List[DataArray]
            Left components of the fitted model.
        components2: DataArray | Dataset | List[DataArray]
            Right components of the fitted model.

        '''
        comps1 = self.data.components1
        comps2 = self.data.components2

        svecs1 = self.preprocessor1.inverse_transform_components(comps1)
        svecs2 = self.preprocessor2.inverse_transform_components(comps2)
        return svecs1, svecs2
    
    def scores(self):
        '''Return the scores of the left and right field.

        The scores in MCA are the projection of the data matrix onto the
        singular vectors of the cross-covariance matrix.
        
        Returns:
        ----------
        scores1: DataArray | Dataset | List[DataArray]
            Left scores.
        scores2: DataArray | Dataset | List[DataArray]
            Right scores.

        '''
        scores1 = self.data.scores1
        scores2 = self.data.scores2

        scores1 = self.preprocessor1.inverse_transform_scores(scores1)
        scores2 = self.preprocessor2.inverse_transform_scores(scores2)
        return scores1, scores2

    def homogeneous_patterns(self, correction=None, alpha=0.05):
        '''Return the homogeneous patterns of the left and right field.

        The homogeneous patterns are the correlation coefficients between the 
        input data and the scores.

        More precisely, the homogeneous patterns `r_{hom}` are defined as

        .. math::
          r_{hom, x} = \\corr \\left(X, A_x \\right)
        .. math::
          r_{hom, y} = \\corr \\left(Y, A_y \\right)

        where :math:`X` and :math:`Y` are the input data, :math:`A_x` and :math:`A_y`
        are the scores of the left and right field, respectively.

        Parameters:
        -------------
        correction: str, default=None
            Method to apply a multiple testing correction. If None, no correction
            is applied.  Available methods are:
            - bonferroni : one-step correction
            - sidak : one-step correction
            - holm-sidak : step down method using Sidak adjustments
            - holm : step-down method using Bonferroni adjustments
            - simes-hochberg : step-up method (independent)
            - hommel : closed method based on Simes tests (non-negative)
            - fdr_bh : Benjamini/Hochberg (non-negative) (default)
            - fdr_by : Benjamini/Yekutieli (negative)
            - fdr_tsbh : two stage fdr correction (non-negative)
            - fdr_tsbky : two stage fdr correction (non-negative)
        alpha: float, default=0.05
            The desired family-wise error rate. Not used if `correction` is None.

        Returns:
        ----------
        patterns1: DataArray | Dataset | List[DataArray]
            Left homogenous patterns.
        patterns2: DataArray | Dataset | List[DataArray]
            Right homogenous patterns.
        pvals1: DataArray | Dataset | List[DataArray]
            Left p-values.
        pvals2: DataArray | Dataset | List[DataArray]
            Right p-values.

        '''
        input_data1 = self.data.input_data1
        input_data2 = self.data.input_data2

        scores1 = self.data.scores1
        scores2 = self.data.scores2

        hom_pat1, pvals1 = pearson_correlation(input_data1, scores1, correction=correction, alpha=alpha)
        hom_pat2, pvals2 = pearson_correlation(input_data2, scores2, correction=correction, alpha=alpha)

        hom_pat1 = self.preprocessor1.inverse_transform_components(hom_pat1)
        hom_pat2 = self.preprocessor2.inverse_transform_components(hom_pat2)

        pvals1 = self.preprocessor1.inverse_transform_components(pvals1)
        pvals2 = self.preprocessor2.inverse_transform_components(pvals2)

        hom_pat1.name = 'left_homogeneous_patterns'
        hom_pat2.name = 'right_homogeneous_patterns'

        pvals1.name = 'pvalues_of_left_homogeneous_patterns'
        pvals2.name = 'pvalues_of_right_homogeneous_patterns'

        return (hom_pat1, hom_pat2), (pvals1, pvals2)

    def heterogeneous_patterns(self, correction=None, alpha=0.05):
        '''Return the heterogeneous patterns of the left and right field.
        
        The heterogeneous patterns are the correlation coefficients between the
        input data and the scores of the other field.
        
        More precisely, the heterogeneous patterns `r_{het}` are defined as
        
        .. math::
          r_{het, x} = \\corr \\left(X, A_y \\right)
        .. math::
          r_{het, y} = \\corr \\left(Y, A_x \\right)
        
        where :math:`X` and :math:`Y` are the input data, :math:`A_x` and :math:`A_y`
        are the scores of the left and right field, respectively.

        Parameters:
        -------------
        correction: str, default=None
            Method to apply a multiple testing correction. If None, no correction
            is applied.  Available methods are: 
            - bonferroni : one-step correction
            - sidak : one-step correction
            - holm-sidak : step down method using Sidak adjustments
            - holm : step-down method using Bonferroni adjustments
            - simes-hochberg : step-up method (independent)
            - hommel : closed method based on Simes tests (non-negative)
            - fdr_bh : Benjamini/Hochberg (non-negative) (default)
            - fdr_by : Benjamini/Yekutieli (negative)
            - fdr_tsbh : two stage fdr correction (non-negative)
            - fdr_tsbky : two stage fdr correction (non-negative)
        alpha: float, default=0.05
            The desired family-wise error rate. Not used if `correction` is None.

        '''
        input_data1 = self.data.input_data1
        input_data2 = self.data.input_data2

        scores1 = self.data.scores1
        scores2 = self.data.scores2

        patterns1, pvals1 = pearson_correlation(input_data1, scores2, correction=correction, alpha=alpha)
        patterns2, pvals2 = pearson_correlation(input_data2, scores1, correction=correction, alpha=alpha)

        patterns1 = self.preprocessor1.inverse_transform_components(patterns1)
        patterns2 = self.preprocessor2.inverse_transform_components(patterns2)

        pvals1 = self.preprocessor1.inverse_transform_components(pvals1)
        pvals2 = self.preprocessor2.inverse_transform_components(pvals2)

        patterns1.name = 'left_heterogeneous_patterns'
        patterns2.name = 'right_heterogeneous_patterns'

        pvals1.name = 'pvalues_of_left_heterogeneous_patterns'
        pvals2.name = 'pvalues_of_right_heterogeneous_patterns'

        return (patterns1, patterns2), (pvals1, pvals2)

    def compute(self, verbose: bool = False):
        '''Computing the model will compute and load all DaskArrays.
        
        Parameters:
        -------------
        verbose: bool, default=False
            If True, print information about the computation process.
            
        '''
        if verbose:
            with ProgressBar():
                self.data.compute(verbose=verbose)
        else:
            self.data.compute(verbose=verbose)


class ComplexMCA(MCA):
    '''Complex Maximum Covariance Analysis (MCA). 

    This class inherits from the MCA class and overloads its methods to implement a version of MCA 
    that uses complex numbers (i.e., applies the Hilbert transform) to capture phase relationships 
    in the input datasets.

    Parameters:
    -------------
    n_modes: int, default=10
        Number of modes to calculate.
    standardize: bool, default=False
        Whether to standardize the input data.
    use_coslat: bool, default=False
        Whether to use cosine of latitude for scaling.
    use_weights: bool, default=False
        Whether to use additional weights.
    padding: str, default='exp'or None
        Padding method to use for the Hilbert transform. Currently, only exponential padding is supported.
    decay_factor: float, default=0.2
        Decay factor for the exponential padding. Only used if `padding` is set to 'exp'.


    Attributes
    ----------
    No additional attributes to the MCA base class.

    Methods
    -------
    fit(data1, data2, dim, weights1=None, weights2=None):
        Fit the model to two datasets.

    transform(data1, data2):
        Not implemented in the ComplexMCA class.

    homogeneous_patterns(correction=None, alpha=0.05):
        Not implemented in the ComplexMCA class.

    heterogeneous_patterns(correction=None, alpha=0.05):
        Not implemented in the ComplexMCA class.
    '''

    def __init__(self, padding='exp', decay_factor=.2, **kwargs):
        super().__init__(**kwargs)
        self._params.update({'padding': padding, 'decay_factor': decay_factor})

    def fit(self, data1: AnyDataObject, data2: AnyDataObject, dim, weights1=None, weights2=None):
        '''Fit the model.

        Parameters:
        -------------
        data1: xr.DataArray or list of xarray.DataArray
            Left input data.
        data2: xr.DataArray or list of xarray.DataArray
            Right input data.
        dim: tuple
            Tuple specifying the sample dimensions. The remaining dimensions 
            will be treated as feature dimensions.
        weights1: xr.DataArray or xr.Dataset or None, default=None
            If specified, the left input data will be weighted by this array.
        weights2: xr.DataArray or xr.Dataset or None, default=None
            If specified, the right input data will be weighted by this array.

        '''

        data1_processed: DataArray = self.preprocessor1.fit_transform(data1, dim, weights2)
        data2_processed: DataArray = self.preprocessor2.fit_transform(data2, dim, weights2)
        
        # apply hilbert transform:
        padding = self._params['padding']
        decay_factor = self._params['decay_factor']
        data1_processed = hilbert_transform(data1_processed, dim='sample', padding=padding, decay_factor=decay_factor)
        data2_processed = hilbert_transform(data2_processed, dim='sample', padding=padding, decay_factor=decay_factor)
        
        decomposer = CrossDecomposer(n_modes=self._params['n_modes'])
        decomposer.fit(data1_processed, data2_processed)

        # Note:
        # - explained variance is given by the singular values of the SVD;
        # - We use the term singular_values_pca as used in the context of PCA:
        # Considering data X1 = X2, MCA is the same as PCA. In this case,
        # singular_values_pca is equivalent to the singular values obtained
        # when performing PCA of X1 or X2.
        singular_values = decomposer.singular_values_
        squared_covariance = singular_values**2
        total_squared_covariance = decomposer.total_squared_covariance_
        # singular_values_pca = np.sqrt(singular_values * (data1_processed.shape[0] - 1))
        singular_vectors1 = decomposer.singular_vectors1_
        singular_vectors2 = decomposer.singular_vectors2_
        norm1 = np.sqrt(singular_values)
        norm2 = np.sqrt(singular_values)

        # Index of the sorted squared covariance
        idx_sorted_modes = squared_covariance.compute().argsort()[::-1]
        idx_sorted_modes.coords.update(squared_covariance.coords)

        # Project the data onto the singular vectors
        scores1 = xr.dot(data1_processed, singular_vectors1) / norm1
        scores2 = xr.dot(data2_processed, singular_vectors2) / norm2

        self.data = ComplexMCADataContainer(
            input_data1=data1_processed,
            input_data2=data2_processed,
            components1=singular_vectors1,
            components2=singular_vectors2,
            scores1=scores1,
            scores2=scores2,
            squared_covariance=squared_covariance,
            total_squared_covariance=total_squared_covariance,
            idx_modes_sorted=idx_sorted_modes,
            norm1=norm1,
            norm2=norm2,
        )
        # Assign analysis relevant meta data
        self.data.set_attrs(self.attrs)

    def components_amplitude(self) -> Tuple[AnyDataObject, AnyDataObject]:
        '''Compute the amplitude of the components.

        Returns
        -------
        xr.DataArray
            Amplitude of the components.

        '''
        comps1 = self.data.components_amplitude1
        comps2 = self.data.components_amplitude2

        comps1 = self.preprocessor1.inverse_transform_components(comps1)
        comps2 = self.preprocessor2.inverse_transform_components(comps2)

        return (comps1, comps2)

    def components_phase(self) -> Tuple[AnyDataObject, AnyDataObject]:
        '''Compute the phase of the components.

        Returns
        -------
        xr.DataArray
            Phase of the components.

        '''
        comps1 = self.data.components_phase1
        comps2 = self.data.components_phase2

        comps1 = self.preprocessor1.inverse_transform_components(comps1)
        comps2 = self.preprocessor2.inverse_transform_components(comps2)

        return (comps1, comps2)
    
    def scores_amplitude(self) -> Tuple[DataArray, DataArray]:
        '''Compute the amplitude of the scores.

        Returns
        -------
        xr.DataArray
            Amplitude of the scores.

        '''
        scores1 = self.data.scores_amplitude1
        scores2 = self.data.scores_amplitude2

        scores1 = self.preprocessor1.inverse_transform_scores(scores1)
        scores2 = self.preprocessor2.inverse_transform_scores(scores2)
        return (scores1, scores2)
    
    def scores_phase(self) -> Tuple[DataArray, DataArray]:
        '''Compute the phase of the scores.

        Returns
        -------
        xr.DataArray
            Phase of the scores.

        '''
        scores1 = self.data.scores_phase1
        scores2 = self.data.scores_phase2

        scores1 = self.preprocessor1.inverse_transform_scores(scores1)
        scores2 = self.preprocessor2.inverse_transform_scores(scores2)

        return (scores1, scores2)

    
    def transform(self, data1: AnyDataObject, data2: AnyDataObject):
        raise NotImplementedError('Complex MCA does not support transform method.')

    def homogeneous_patterns(self, correction=None, alpha=0.05):
        raise NotImplementedError('Complex MCA does not support homogeneous_patterns method.')
    
    def heterogeneous_patterns(self, correction=None, alpha=0.05):
        raise NotImplementedError('Complex MCA does not support heterogeneous_patterns method.')

