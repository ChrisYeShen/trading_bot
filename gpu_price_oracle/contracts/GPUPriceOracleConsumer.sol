// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IGPUPriceOracle {
    function getPrice(string calldata gpuName)
        external
        view
        returns (
            uint256 hardwarePriceUsdCents,
            uint256 rentalPriceUsdMilliCentsPerHour,
            uint256 updatedAt,
            uint256 numSources
        );
}

/**
 * @title GPUPriceOracleConsumer
 * @notice Example contract that reads GPU prices from the oracle.
 *         Demonstrates how to integrate the oracle in other protocols.
 */
contract GPUPriceOracleConsumer {
    IGPUPriceOracle public immutable oracle;
    uint256 public constant MAX_PRICE_AGE = 1 hours;

    constructor(address oracleAddress) {
        oracle = IGPUPriceOracle(oracleAddress);
    }

    /**
     * @notice Returns rental price in USD cents per GPU-hour, reverting if stale.
     */
    function getFreshRentalPrice(string calldata gpuName)
        external
        view
        returns (uint256 usdCentsPerHour)
    {
        (, uint256 rentalMilliCents, uint256 updatedAt,) = oracle.getPrice(gpuName);
        require(block.timestamp - updatedAt <= MAX_PRICE_AGE, "oracle price stale");
        // convert milli-cents → cents (round up)
        return (rentalMilliCents + 999) / 1000;
    }

    /**
     * @notice Returns hardware price in USD cents, reverting if stale.
     */
    function getFreshHardwarePrice(string calldata gpuName)
        external
        view
        returns (uint256 usdCents)
    {
        (uint256 hw,, uint256 updatedAt,) = oracle.getPrice(gpuName);
        require(block.timestamp - updatedAt <= MAX_PRICE_AGE, "oracle price stale");
        return hw;
    }
}
