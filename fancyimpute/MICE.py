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

from __future__ import absolute_import, print_function, division

import numpy as np

from .bayesian_ridge_regression import BayesianRidgeRegression
from .solver import Solver


class MICE(Solver):
    """
    Basic implementation of MICE package from R.
    This version assumes all of the columns are ordinal,
    and uses ridge regression.

    Parameters
    ----------
    visit_sequence : str
        Possible values: "monotone" (default), "roman", "arabic", "revmonotone".

    n_imputations : int
        Defaults to 100

    n_burn_in : int
        Defaults to 10

    impute_type : str
        "row" means classic PMM, "col" (default) means fill in linear preds.

    n_pmm_neighbors : int
        Number of nearest neighbors for PMM, defaults to 5.

    model : predictor function
        A model that has fit, predict, and predict_dist methods.
        Defaults to BayesianRidgeRegression(lambda_reg=0.001).
        Note that the regularization parameter lambda_reg
        is by default scaled by np.linalg.norm(np.dot(X.T,X)).
        Sensible lambda_regs to try: 0.25, 0.1, 0.01, 0.001, 0.0001.

    add_ones : boolean
        Whether to add a constant column of ones. Defaults to True.
            
    n_nearest_columns : int
        Number of other columns to use to estimate current column.
        Useful when number of columns is huge. 
        Default is to use all columns.
            
    verbose : boolean
    """

    def __init__(
            self,
            visit_sequence='monotone',  # order in which we visit the columns
            n_imputations=100,
            n_burn_in=10,  # this many replicates will be thrown away
            n_pmm_neighbors=5,  # number of nearest neighbors in PMM
            impute_type='col', # also can be pmm
            model=BayesianRidgeRegression(lambda_reg=0.001),
            add_ones=True,
            n_nearest_columns=np.infty,
            verbose=True):
        """
        Parameters
        ----------
        visit_sequence : str
            Possible values: "monotone" (default), "roman", "arabic", 
                "revmonotone".

        n_imputations : int
            Defaults to 100

        n_burn_in : int
            Defaults to 10

        impute_type : str
            "ppm" is probablistic moment matching.
            "col" (default) means fill in with samples from posterior predictive
                distribution.

        n_pmm_neighbors : int
            Number of nearest neighbors for PMM, defaults to 5.

        model : predictor function
            A model that has fit, predict, and predict_dist methods.
        Defaults to BayesianRidgeRegression(lambda_reg=0.001).
        Note that the regularization parameter lambda_reg
        is by default scaled by np.linalg.norm(np.dot(X.T,X)).
        Sensible lambda_regs to try: 0.25, 0.1, 0.01, 0.001, 0.0001.

        add_ones : boolean
            Whether to add a constant column of ones. Defaults to True.
            
        n_nearest_columns : int
            Number of other columns to use to estimate current column.
            Useful when number of columns is huge. 
            Default is to use all columns.

        verbose : boolean
        """
        self.visit_sequence = visit_sequence
        self.n_imputations = n_imputations
        self.n_burn_in = n_burn_in
        self.n_pmm_neighbors = n_pmm_neighbors
        self.impute_type = impute_type
        self.model = model
        self.add_ones = add_ones
        self.n_nearest_columns = n_nearest_columns
        self.verbose = verbose

    def perform_imputation_round(
            self,
            X_filled,
            missing_mask,
            visit_indices):
        """
        Does one entire round-robin set of updates.
        """
        n_rows, n_cols = X_filled.shape
        # since we're accessing the missing mask one column at a time,
        # lay it out so that columns are contiguous
        missing_mask = np.asarray(missing_mask, order="F")
        observed_mask = ~missing_mask
        if (n_cols - int(self.add_ones) > self.n_nearest_columns):
            abs_correlation_matrix = np.abs(np.corrcoef(X_filled.T))
        n_missing_for_each_column = missing_mask.sum(axis=0)
        for col_idx in visit_indices:
            missing_mask_col = missing_mask[:, col_idx]  # missing mask for this column
            n_missing_for_this_col = n_missing_for_each_column[col_idx]
            if n_missing_for_this_col > 0:  # if we have any missing data at all
                observed_row_mask_for_col = observed_mask[:, col_idx]
                other_cols = np.array(list(range(0, col_idx)) + list(range(col_idx + 1, n_cols)))
                output = X_filled[observed_row_mask_for_col, col_idx]
                if (n_cols - int(self.add_ones) > self.n_nearest_columns):
                    # probability of column draw is proportional to absolute 
                    # pearson correlation
                    p = abs_correlation_matrix[col_idx,other_cols]
                    if self.add_ones:
                        p = p[:-1]/p[:-1].sum()
                        other_cols = np.random.choice(other_cols[:-1],
                                                      self.n_nearest_columns,
                                                      replace=False,
                                                      p=p)
                        other_cols = np.append(other_cols,other_cols[:-1])
                    else:
                        p /= p.sum()
                        other_cols = np.random.choice(other_cols,
                                                      self.n_nearest_columns,
                                                      replace=False,
                                                      p=p)

                inputs = X_filled[np.ix_(observed_row_mask_for_col, other_cols)]
                brr = self.model
                brr.fit(inputs, output, inverse_covariance=None)

                # Now we choose the row method (PMM) or the column method.
                if self.impute_type == 'pmm':  # this is the PMM procedure
                    # predict values for missing values using random beta draw
                    X_missing = X_filled[np.ix_(missing_mask_col, other_cols)]
                    col_preds_missing = brr.predict(X_missing, random_draw=True)
                    # predict values for observed values using best estimated beta
                    X_observed = X_filled[np.ix_(observed_row_mask_for_col, other_cols)]
                    col_preds_observed = brr.predict(X_observed, random_draw=False)
                    # for each missing value, find its nearest neighbors in the observed values
                    D = np.abs(col_preds_missing[:, np.newaxis] - col_preds_observed)  # distances
                    # take top k neighbors
                    k = np.minimum(self.n_pmm_neighbors, len(col_preds_observed) - 1)
                    NN = np.argpartition(D, k, 1)[:, :k]  # <- bottleneck!
                    # pick one of the nearest neighbors at random! that's right!
                    NN_sampled = [np.random.choice(NN_row) for NN_row in NN]
                    # set the missing values to be the values of the nearest
                    # neighbor in the output space
                    X_filled[missing_mask_col, col_idx] = \
                        X_filled[observed_row_mask_for_col, col_idx][NN_sampled]
                elif self.impute_type == 'col':
                    X_missing = X_filled[np.ix_(missing_mask_col, other_cols)]
                    # predict values for missing values using posterior predictive draws
                    # see the end of this:
                    # https://www.cs.utah.edu/~fletcher/cs6957/lectures/BayesianLinearRegression.pdf
                    # X_filled[missing_mask_col,col] = \
                    #   brr.posterior_predictive_draw(X_missing)
                    mus, sigmas_squared = brr.predict_dist(X_missing)
                    X_filled[missing_mask_col, col_idx] = \
                        np.random.normal(mus, np.sqrt(sigmas_squared))
        return X_filled

    def initialize(self, X, missing_mask, visit_indices):
        """
        Initialize the missing values by simple sampling from the same column.
        """
        X_filled = X.copy()
        observed_mask = ~missing_mask
        for col_idx in visit_indices:
            missing_mask_col = missing_mask[:, col_idx]
            if np.sum(missing_mask_col) > 0:
                observed_row_mask_for_col = observed_mask[:, col_idx]
                observed_col = X_filled[observed_row_mask_for_col, col_idx]
                n_missing = np.sum(missing_mask_col)
                random_values = np.random.choice(observed_col, n_missing)
                X_filled[missing_mask_col, col_idx] = random_values
        return X_filled

    def get_visit_indices(self, missing_mask):
        """
        Decide what order we will update the columns.
        As a homage to the MICE package, we will have 4 options of
        how to order the updates.
        """
        n_rows, n_cols = missing_mask.shape
        if self.visit_sequence == 'roman':
            return np.arange(n_cols)
        elif self.visit_sequence == 'arabic':
            return np.arange(n_cols - 1, -1, -1)  # same as np.arange(d)[::-1]
        elif self.visit_sequence == 'monotone':
            return np.argsort(missing_mask.sum(0))[::-1]
        elif self.visit_sequence == 'revmonotone':
            return np.argsort(missing_mask.sum(0))
        else:
            raise ValueError("Invalid choice for visit order: %s" % self.visit_sequence)

    def multiple_imputations(self, X):
        """
        Expects 2d float matrix with NaN entries signifying missing values

        Returns a sequence of arrays of the imputed missing values
        of length self.n_imputations, and a mask that specifies where these values
        belong in X.
        """

        self._check_input(X)
        missing_mask = np.isnan(X)
        self._check_missing_value_mask(missing_mask)
        visit_indices = self.get_visit_indices(missing_mask)
        n_rows = len(X)
        if self.add_ones:
            X = np.column_stack((X, np.ones(n_rows)))
            missing_mask = np.column_stack([
                missing_mask,
                np.zeros(n_rows, dtype=missing_mask.dtype)
            ])

        X_filled = self.initialize(
            X,
            missing_mask=missing_mask,
            visit_indices=visit_indices)

        # now we jam up in the usual fashion for n_burn_in + n_imputations iterations
        results_list = []  # all of the imputed values, in a flattened format
        total_rounds = self.n_burn_in + self.n_imputations

        for m in range(total_rounds):
            if self.verbose:
                print("[MICE] Imputation round %d/%d:" % (
                    m + 1, total_rounds))
            X_filled = self.perform_imputation_round(
                X_filled=X_filled,
                missing_mask=missing_mask,
                visit_indices=visit_indices)
            if m >= self.n_burn_in:
                results_list.append(X_filled[missing_mask])
        if self.add_ones:
            # chop off the missing mask corresponding to the constant ones
            missing_mask = missing_mask[:, :-1]
        return np.array(results_list), missing_mask

    def complete(self, X):
        X_completed = X.copy()
        imputed_arrays, missing_mask = self.multiple_imputations(X)
        # average the imputed values for each feature
        average_imputated_values = imputed_arrays.mean(axis=0)
        X_completed[missing_mask] = average_imputated_values
        return X_completed
