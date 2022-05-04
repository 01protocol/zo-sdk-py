# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- BREAKING: Remove `MarketInfo.funding_rate` in favour of `MarketInfo.funding_{info,sample_start_time}` for new funding formula.

## [0.1.2] - 2022-04-08

- Add `MarketInfo.{mark,index}_{price,twap}` and `MarketInfo.funding_rate`.
- Add `commitment` parameter to `Zo.refresh()`.
- Fix `Zo.orderbook` missing when `load_margin=False`.

## [0.1.1] - 2022-03-08

- Fix broken `Zo.place_order`.

## [0.1.0] - 2022-03-06

- Initial release.
