#!/usr/bin/env Rscript
# Deterministic R reference generator for T7 — Chow known-date structural break test (D4=B)
# Compatible with base R + strucchange::sctest(y ~ x, type = "Chow", point = bp)

suppressPackageStartupMessages({
  library(jsonlite)
})

chow_exact <- function(y, x, bp) {
  n <- length(y)
  X <- cbind(1.0, x)
  k <- ncol(X)
  rss_p <- sum(lm.fit(X, y)$residuals^2)
  rss_1 <- sum(lm.fit(X[1:bp, , drop = FALSE], y[1:bp])$residuals^2)
  rss_2 <- sum(lm.fit(X[(bp + 1):n, , drop = FALSE], y[(bp + 1):n])$residuals^2)
  rss_u <- rss_1 + rss_2
  df1 <- k
  df2 <- n - 2L * k
  f_stat <- ((rss_p - rss_u) / df1) / (rss_u / df2)
  p_val <- pf(f_stat, df1 = df1, df2 = df2, lower.tail = FALSE)
  list(statistic = unname(f_stat), p_value = unname(p_val), df1 = df1, df2 = df2)
}

idx <- seq_len(60L)
x_vec <- round(sin(idx * 0.37) + 0.25 * cos(idx * 0.83), 8)
eps_vec <- round(0.35 * cos(idx * 1.13) + 0.20 * sin(idx * 2.07), 8)

# Case 1 (null / stable single regime): y = 10 + 2.0 * x + eps
y_null <- round(10.0 + 2.0 * x_vec + eps_vec, 8)
res_null <- chow_exact(y_null, x_vec, 30L)

# Case 2 (planted structural break at point = 30): intercept +2.4 and slope +1.1 after t = 30
shift <- ifelse(idx > 30L, 2.4 + 1.1 * x_vec, 0.0)
y_break <- round(y_null + shift, 8)
res_break <- chow_exact(y_break, x_vec, 30L)

if (requireNamespace("strucchange", quietly = TRUE)) {
  sc_break <- strucchange::sctest(y_break ~ x_vec, type = "Chow", point = 30L)
  stopifnot(abs(sc_break$statistic - res_break$statistic) < 1e-10)
  stopifnot(abs(sc_break$p.value - res_break$p_value) < 1e-10)
}

cat(toJSON(list(null_case = res_null, break_case = res_break), auto_unbox = TRUE, digits = 12), "\n")
