window.CheckStockWbCalculator = (() => {
    'use strict';
    function calculatedAdvertisingRub(retail, drr, buyoutPercent) {
        if (retail === null || drr === null || buyoutPercent === null) return null;
        var buyoutRatio = Math.min(Math.max(buyoutPercent, 0), 100) / 100;
        var amount = ((Math.max(retail, 0) * Math.max(drr, 0)) / 100) * buyoutRatio;
        return Math.round(amount * 100) / 100;
    }
    function calculatedDrrPercent(retail, advertisingRub, buyoutPercent) {
        if (
            retail === null ||
            retail <= 0 ||
            advertisingRub === null ||
            buyoutPercent === null ||
            buyoutPercent <= 0
        )
            return null;
        return (advertisingRub / ((retail * buyoutPercent) / 100)) * 100;
    }
    return { calculatedAdvertisingRub, calculatedDrrPercent };
})();
