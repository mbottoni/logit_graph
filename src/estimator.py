import numpy as np
from scipy.optimize import minimize
import torch

from sklearn.linear_model import LogisticRegression
import networkx as nx

import statsmodels.api as sm
import statsmodels.formula.api as smf

max_val = np.nan
eps = 1e-5

class NegativeLogLikelihoodLoss(torch.nn.Module):
    def __init__(self, graph):
        super(NegativeLogLikelihoodLoss, self).__init__()
        self.graph = torch.tensor(graph, dtype=torch.float32)  # Ensure graph is a PyTorch tensor
        self.n = graph.shape[0]
        self.p = 0

    def logistic_probability(self, sum_degrees):
        num = 1
        denom = 1 + 1 * torch.exp(sum_degrees)
        return num / denom

    def degree_vertex(self, vertex, p):
        def get_neighbors(v):
            return [i for i, x in enumerate(self.graph[v]) if x == 1]

        def get_degree(v):
            return sum(self.graph[v])

        if p == 0:
            return [get_degree(vertex)]
        if p == 1:
            neighbors = get_neighbors(vertex)
            return [get_degree(vertex)] + [get_degree(neighbor) for neighbor in neighbors]

        visited, current_neighbors = set([vertex]), get_neighbors(vertex)
        for _ in range(int(p) - 1):
            next_neighbors = []
            for v in current_neighbors:
                next_neighbors.extend([nv for nv in get_neighbors(v) if nv not in visited])
                visited.add(v)
            current_neighbors = list(set(next_neighbors))
        return [get_degree(vertex)] + [get_degree(neighbor) for neighbor in current_neighbors]

    def get_sum_degrees(self, vertex, p=1):
        return sum(self.degree_vertex(vertex, p))

    def forward(self, params):
        alpha, beta, sigma = params
        likelihood = 0.0

        # Iterate over all possible edges
        for i in range(self.n):
            for j in range(i, self.n):
                degrees_i = self.get_sum_degrees(i, self.p)
                degrees_j = self.get_sum_degrees(j, self.p)
                sum_degrees_raw = ( alpha * degrees_i + beta * degrees_j )
                sum_degrees = ( sum_degrees_raw + sigma )
                #sum_degrees = torch.sum(self.graph[i]) + torch.sum(self.graph[j])
                p_ij = self.logistic_probability(sum_degrees)

                # Adding a small constant to probabilities to avoid log(0)
                #if self.graph[i, j] == 1:
                #    likelihood += torch.log(p_ij + eps)
                #else:
                #    likelihood += torch.log(1 - p_ij + eps)
                if self.graph[i, j] == 1:
                    likelihood += torch.log(p_ij + eps)  # Adding a small constant to avoid log(0)
                else:
                    likelihood += torch.log(1 - p_ij + eps)


        return -likelihood  # Return negative likelihood

class MLEGraphModelEstimator:
    def __init__(self, graph):
        self.graph = graph  # The observed adjacency matrix
        self.n = graph.shape[0]  # Number of nodes in the graph
        self.params_history = []  # History of parameters during optimization

    def logistic_probability(self, sum_degrees):
        num = 1
        denom = 1 + 1 * torch.exp(sum_degrees)
        return num / denom

    def likelihood_function(self, params):
        """Negative log-likelihood function to be minimized."""
        alpha, beta, sigma = params
        likelihood = 0

        for i in range(self.n):
            for j in range(i, self.n):
                #sum_degrees = np.sum(self.graph[i]) + np.sum(self.graph[j])  # Sum of degrees of nodes i and j
                #p_ij = self.logistic_probability(c, beta, sum_degrees)  # Probability of edge (i, j)
                degrees_i = self.get_sum_degrees(i, self.p)
                degrees_j = self.get_sum_degrees(j, self.p)
                sum_degrees_raw = ( alpha * degrees_i + beta * degrees_j )
                sum_degrees = ( sum_degrees_raw + sigma )
                #sum_degrees = torch.sum(self.graph[i]) + torch.sum(self.graph[j])
                p_ij = self.logistic_probability(sum_degrees)

                if 1-p_ij+eps <= 0:
                    return max_val

                if self.graph[i, j] == 1:
                    try:
                        likelihood += np.log(abs(p_ij + eps))  # Adding a small constant to avoid log(0)
                    except:
                        return np.float(max_val)
                else:
                    try:
                        likelihood += np.log(abs(1 - p_ij + eps))
                    except:
                        return np.float(max_val)

        return likelihood  # Negative because we minimize in the optimization routine

    def estimate_parameters_torch(self, initial_guess=[0.5, 0.1], learning_rate=0.01, max_iter=1000):
            alpha, beta, sigma = [torch.tensor(x, dtype=torch.float32, requires_grad=True) for x in initial_guess]
            optimizer = torch.optim.SGD([alpha, beta, sigma], lr=learning_rate)  # Using SGD optimizer from PyTorch

            # Instantiate the loss function class with the graph
            loss_function = NegativeLogLikelihoodLoss(self.graph)
            for _ in range(max_iter):
                optimizer.zero_grad()  # Clear previous gradients
                # Compute the loss by passing the parameters to the loss function instance
                loss = loss_function([alpha, beta, sigma])
                # Call backward on the loss tensor to compute gradients
                loss.backward()
                # Update parameters based on gradients
                optimizer.step()

                # Store parameters
                self.params_history.append([alpha.item(), beta.item(), sigma.item()])
                print(f"Current parameters: alpha={alpha.item()}, beta={beta.item()}, sigma={sigma.item()}, Loss={loss.item()}")

            print(f"Optimization completed. Estimated parameters: alpha={alpha.item()}, beta={beta.item()}, sigma={sigma.item()}")
            return alpha.item(), beta.item(), sigma.item()

class LogitRegEstimator:
    def __init__(self, graph):
        self.graph = graph  # The observed adjacency matrix
        self.n = graph.shape[0]  # Number of nodes in the graph

    def estimate_parameters(self, l1_wt=1.0, alpha=0.1):
        """
        Estimate parameters using logistic regression with regularization.
        
        Args:
        l1_wt (float): The L1 weight (0 for pure L2, 1 for pure L1).
        alpha (float): Regularization strength. Larger values specify stronger regularization.
        """
        G = nx.Graph(self.graph)

        edges = list(G.edges())
        non_edges = list(nx.non_edges(G))

        # Combine edges and non-edges to form the dataset
        data = edges + non_edges
        labels = [1] * len(edges) + [0] * len(non_edges)

        # Feature extraction: degrees of the vertices
        #normalization = self.n - 1
        normalization = 1
        features = np.array([(G.degree(i) / normalization, G.degree(j) / normalization) for i, j in data])

        # Add a constant term for the intercept
        features = sm.add_constant(features)

        # Logistic Regression Model using statsmodels with regularization
        model = sm.Logit(labels, features)
        
        # Fit the model with regularization
        if l1_wt in [0, 1]:
            # Pure L1 or L2 regularization
            result = model.fit_regularized(method='l1' if l1_wt == 1 else 'l2', alpha=alpha, disp=0)
        else:
            # Elastic Net (combination of L1 and L2)
            result = model.fit_regularized(L1_wt=l1_wt, alpha=alpha, disp=0)

        # Print summary
        print(result.summary2())

        # Extract parameters and p-values
        params = result.params
        p_values = result.pvalues  # Note: p-values can be unreliable in regularized regressions

        return params, p_values
