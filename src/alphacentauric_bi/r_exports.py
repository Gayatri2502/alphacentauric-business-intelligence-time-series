"""Self-contained R evidence exports; no R process or paid API is invoked."""
import json
from pathlib import Path

VERIFY = r'''
report <- evidence$report
history <- evidence$history
forecast <- evidence$forecast
backtest <- evidence$backtest
checks <- data.frame(check=character(), passed=logical())
record_check <- function(name, passed) {
  checks <<- rbind(checks, data.frame(check=name, passed=isTRUE(passed)))
}
record_check("History is chronological and unique", all(diff(as.Date(history$period)) > 0))
record_check("Forecast follows observed history", min(as.Date(forecast$period)) > max(as.Date(history$period)))
record_check("Measured and forecast values are finite", all(is.finite(history$observed)) && all(is.finite(as.matrix(forecast[, c("forecast", "lower", "upper")]))))
record_check("Forecast bounds contain point estimates", all(forecast$lower <= forecast$forecast & forecast$forecast <= forecast$upper))
record_check("Every backtest follows its training end", all(as.Date(substr(backtest$train_end, 1, 10)) < as.Date(substr(backtest$period, 1, 10))))
cv <- subset(backtest, partition == "rolling_validation")
final <- subset(backtest, partition == "untouched_final_test")
record_check("Final test follows rolling validation", nrow(final) > 0 && max(as.Date(substr(cv$period, 1, 10))) < min(as.Date(substr(final$period, 1, 10))))
metric_values <- function(d) {
  error <- d$actual - d$prediction
  denom <- abs(d$actual) + abs(d$prediction)
  ratios <- numeric(length(error)); nz <- denom != 0
  ratios[nz] <- 2 * abs(error[nz]) / denom[nz]
  c(mae=mean(abs(error)), rmse=sqrt(mean(error^2)), smape=100*mean(ratios),
    coverage=100*mean(d$actual >= d$lower & d$actual <= d$upper))
}
comparison <- do.call(rbind, lapply(c("rolling_validation", "untouched_final_test"), function(part) {
  values <- metric_values(subset(backtest, partition == part))
  expected <- if (part == "rolling_validation") report$validation else report$final_test
  data.frame(partition=part, metric=names(values), recalculated=as.numeric(values),
             reported=as.numeric(unlist(expected[names(values)])), row.names=NULL)
}))
comparison$difference <- comparison$recalculated - comparison$reported
for (i in seq_len(nrow(comparison))) record_check(paste(comparison$partition[i], comparison$metric[i]), abs(comparison$difference[i]) <= 1e-7 * max(1, abs(comparison$reported[i])))
train <- history$observed[as.Date(history$period) <= as.Date(substr(report$final_test$train_end, 1, 10))]
naive_rmse <- sqrt(mean((final$actual - tail(train, 1))^2))
record_check("Final-test naive baseline RMSE matches", abs(naive_rmse - report$final_test$naive_rmse) <= 1e-7 * max(1, naive_rmse))
lag <- as.integer(report$configuration$season)
if (lag <= 1 || length(train) <= lag) lag <- 1L
scale <- mean(abs(train[(lag+1):length(train)] - train[1:(length(train)-lag)]))
if (scale > 1e-12 && !is.null(report$final_test$mase)) record_check("Final-test MASE matches", abs(mean(abs(final$actual-final$prediction))/scale - report$final_test$mase) <= 1e-7 * max(1, report$final_test$mase))
cagr <- function(a, b, days) if (a > 0 && b > 0 && days > 0) 100 * ((b/a)^(365.2425/days)-1) else NA_real_
if (!is.null(report$summary$historical_cagr_percent)) {
  value <- cagr(history$observed[1], tail(history$observed,1), as.numeric(max(as.Date(history$period))-min(as.Date(history$period))))
  record_check("Historical annualized growth matches", isTRUE(all.equal(value, report$summary$historical_cagr_percent, tolerance=1e-7)))
}
if (!is.null(report$summary$forecast_cagr_percent)) {
  value <- cagr(tail(history$observed,1), tail(forecast$forecast,1), as.numeric(max(as.Date(forecast$period))-max(as.Date(history$period))))
  record_check("Projected annualized growth matches", isTRUE(all.equal(value, report$summary$forecast_cagr_percent, tolerance=1e-7)))
}
print(checks, row.names=FALSE)
print(comparison, row.names=FALSE)
if (any(!checks$passed)) warning("Evidence verification failed. Inspect checks and comparison before relying on this report.")
'''

PLOTS = r'''
period <- as.Date(history$period); future <- as.Date(forecast$period)
bounds <- range(c(history$observed, forecast$lower, forecast$upper), finite=TRUE)
plot(period, history$observed, type="l", col="#167b6b", xlim=range(c(period,future)), ylim=bounds,
     xlab="Period", ylab=report$configuration$unit, main="History and forecast")
polygon(c(future,rev(future)), c(forecast$lower,rev(forecast$upper)), col=adjustcolor("#8b6bb1",alpha.f=.2), border=NA)
lines(c(tail(period,1),future), c(tail(history$observed,1),forecast$forecast), col="#7653a2", lty=2, lwd=2)
legend("topleft",c("Observed","Forecast","Displayed nominal interval"),col=c("#167b6b","#7653a2","#8b6bb1"),lty=c(1,2,1),bty="n")
if (all(c("upside","downside") %in% names(forecast))) {
  plot(future, forecast$forecast, type="l", ylim=range(forecast[,c("forecast","upside","downside")]), col="#167b6b", xlab="Period", ylab=report$configuration$unit, main="Scenario assumptions")
  lines(future,forecast$upside,col="#7653a2",lty=2);lines(future,forecast$downside,col="#b67923",lty=3)
  legend("topleft",c("Baseline","Upside assumption","Downside assumption"),col=c("#167b6b","#7653a2","#b67923"),lty=c(1,2,3),bty="n")
}
leaderboard <- report$leaderboard
par(mar=c(9,4,3,1))
barplot(leaderboard$rmse,names.arg=leaderboard$model,las=2,col="#7653a2",ylab="Rolling RMSE",main="Candidate model errors")
par(mar=c(5,4,4,2))
final_period <- as.Date(substr(final$period,1,10))
plot(final_period,final$actual,type="b",ylim=range(c(final$actual,final$prediction)),col="#167b6b",xlab="Final test period",ylab=report$configuration$unit,main="Untouched final test")
lines(final_period,final$prediction,type="b",col="#7653a2",lty=2)
legend("topleft",c("Actual","Predicted"),col=c("#167b6b","#7653a2"),lty=c(1,2),bty="n")
plot(final_period,final$actual-final$prediction,type="h",lwd=3,col="#7653a2",xlab="Period",ylab="Actual minus predicted",main="Final-test residuals");abline(h=0,lty=2)
'''


def export_r(folder, report, history, forecast, backtest):
    folder=Path(folder)
    payload={'report':report,'history':history.to_dict('records'),'forecast':forecast.to_dict('records'),'backtest':backtest.to_dict('records')}
    # JSON quoted as an R string; data never becomes executable R or Markdown.
    quoted=json.dumps(json.dumps(payload,ensure_ascii=True,allow_nan=False),ensure_ascii=True)
    setup='if (!requireNamespace("jsonlite", quietly=TRUE)) stop("Install jsonlite to read embedded evidence.")\nevidence <- jsonlite::fromJSON('+quoted+')\n'
    (folder/'verification.R').write_text('# Alphacentauric independent evidence checks in R\n# Requires jsonlite. No model refit, API call or source download.\n'+setup+VERIFY+'\n# Inspect configurations and fitted parameters:\nstr(report$configuration)\nstr(report$model_parameters)\n',encoding='utf-8')
    md='''---
title: "Alphacentauric Business Intelligence"
subtitle: "Market analysis and independent R evidence checks"
output:
  html_document:
    toc: true
    toc_depth: 2
    number_sections: true
    self_contained: true
---

This document embeds its data and can be knitted independently of the dashboard.
Requires R packages **rmarkdown** and **jsonlite**, plus Pandoc (included with RStudio).
Render with `rmarkdown::render("report.Rmd")`. No external data or paid API is used.

# Findings and source

```{r setup, include=FALSE}
knitr::opts_chunk$set(echo=FALSE, warning=TRUE, message=FALSE, fig.width=9, fig.height=5)
'''+setup+'''report <- evidence$report
```

```{r findings}
cat(report$answer, "\\n\\n")
cat("Created:", report$created_at, "\\nModel:", report$model, "\\nSource:", report$source$filename, "\\nSHA-256:", report$source$sha256, "\\n")
str(report$source$register)
```

# Independent R checks

These checks recompute errors, coverage, the final-test naive baseline, applicable
CAGR and final-test MASE from embedded exported observations. They check chronology
and interval ordering. They do not validate source credibility, guarantee future
coverage, or independently refit the Python model. Expand code in this editable
R Markdown source to add your own diagnostics or model experiments.

```{r verification, echo=TRUE}
'''+VERIFY+'''```

# Graphical evidence

Shaded ranges are nominal prediction intervals. Scenario adjustments are planning
assumptions, not probabilities. Model-selection errors can be optimistic; the
final-test outcomes were reserved before the production refit.

```{r charts}
'''+PLOTS+'''```

# Exploratory R diagnostics

Autocorrelation is descriptive. Trend can inflate level-series correlations;
these charts do not establish stationarity, causality or forecast accuracy.

```{r autocorrelation}
acf(history$observed, lag.max=min(36L, nrow(history)-1L), main="Historical ACF")
acf(diff(history$observed), lag.max=min(36L, nrow(history)-2L), main="First-difference ACF")
```

# Forecast values

```{r forecast-table}
knitr::kable(forecast, digits=4)
```

# Settings and fitted parameters

```{r parameters}
str(report$configuration)
str(report$model_parameters)
str(report$calibration)
str(report$break_screen)
```

# Analyst commentary

```{r notes}
cat(if (is.null(report$analyst_notes) || !nzchar(report$analyst_notes)) "No analyst commentary supplied." else report$analyst_notes)
```

# Limits and reproducibility

```{r limitations}
cat(paste(report$limitations, collapse="\\n\\n"))
str(report$versions)
sessionInfo()
```
'''
    (folder/'report.Rmd').write_text(md,encoding='utf-8')
