#!/usr/bin/env Rscript
# Deterministic R reference generator for T12 — Little's (1988) multivariate Gaussian EM MCAR test (D4=B)
# Compatible with naniar::mcar_test / norm::em.norm ML formulation

suppressPackageStartupMessages({
  library(jsonlite)
})

little_mcar_em <- function(Y, max_iter = 500L, tol = 1e-9, ridge = 1e-8) {
  obs <- !is.na(Y)
  keep <- rowSums(obs) > 0L
  Y <- Y[keep, , drop = FALSE]
  obs <- obs[keep, , drop = FALSE]
  N <- nrow(Y)
  P <- ncol(Y)
  keys <- apply(obs, 1L, function(r) paste(as.integer(r), collapse = ""))
  pats <- split(seq_len(N), keys)

  mu <- vapply(seq_len(P), function(j) mean(Y[obs[, j], j]), numeric(1))
  Y_imp <- Y
  for (j in seq_len(P)) Y_imp[!obs[, j], j] <- mu[j]
  Yc <- sweep(Y_imp, 2L, mu, "-")
  Sigma <- crossprod(Yc) / N + diag(ridge, P)

  for (it in seq_len(max_iter)) {
    sum_y <- numeric(P)
    sum_yy <- matrix(0.0, P, P)
    for (rows in pats) {
      m_g <- length(rows)
      mask <- obs[rows[1L], ]
      o <- which(mask)
      m <- which(!mask)
      if (length(m) == 0L) {
        Yo <- Y[rows, o, drop = FALSE]
        sum_y <- sum_y + colSums(Yo)
        sum_yy <- sum_yy + crossprod(Yo)
      } else {
        Yo <- Y[rows, o, drop = FALSE]
        Soo_inv <- solve(Sigma[o, o, drop = FALSE] + diag(ridge, length(o)))
        Smo <- Sigma[m, o, drop = FALSE]
        B <- Smo %*% Soo_inv
        cond_cov <- Sigma[m, m, drop = FALSE] - B %*% t(Smo)
        Yhat <- matrix(0.0, m_g, P)
        Yhat[, o] <- Yo
        Yhat[, m] <- sweep( sweep(Yo, 2L, mu[o], "-") %*% t(B), 2L, mu[m], "+" )
        sum_y <- sum_y + colSums(Yhat)
        sum_yy <- sum_yy + crossprod(Yhat)
        sum_yy[m, m] <- sum_yy[m, m] + m_g * cond_cov
      }
    }
    mu_new <- sum_y / N
    Sigma_new <- (sum_yy / N) - tcrossprod(mu_new) + diag(ridge, P)
    if (max(abs(mu_new - mu), abs(Sigma_new - Sigma)) < tol) {
      mu <- mu_new
      Sigma <- Sigma_new
      break
    }
    mu <- mu_new
    Sigma <- Sigma_new
  }

  d2 <- 0.0
  sum_pj <- 0L
  for (rows in pats) {
    o <- which(obs[rows[1L], ])
    sum_pj <- sum_pj + length(o)
    ybar <- colMeans(Y[rows, o, drop = FALSE])
    diff <- ybar - mu[o]
    d2 <- d2 + length(rows) * as.numeric(t(diff) %*% solve(Sigma[o, o, drop = FALSE]) %*% diff)
  }
  df <- sum_pj - P
  p_val <- pchisq(d2, df = df, lower.tail = FALSE)
  list(statistic = d2, p_value = p_val, df = df)
}

idx <- seq_len(60L)
x1 <- round(sin(idx * 0.53) + 0.30 * cos(idx * 1.41), 8)
x2 <- round(0.60 * x1 + 0.80 * cos(idx * 0.91), 8)
x3 <- round(-0.40 * x1 + 0.50 * x2 + 0.70 * sin(idx * 1.77), 8)

# Case 1 (MCAR): periodic index missingness completely unrelated to values
Y_mcar <- cbind(x1, x2, x3)
Y_mcar[seq(5L, 60L, by = 6L), 2L] <- NA_real_
Y_mcar[seq(3L, 60L, by = 9L), 3L] <- NA_real_

# Case 2 (MAR): missingness in col 2 & 3 strongly concentrated when x1 > 0.35
Y_mar <- cbind(x1, x2, x3)
Y_mar[x1 > 0.35, 2L] <- NA_real_
Y_mar[x1 < -0.75, 3L] <- NA_real_

cat(toJSON(list(mcar_case = little_mcar_em(Y_mcar), mar_case = little_mcar_em(Y_mar)), auto_unbox = TRUE, digits = 12), "\n")
