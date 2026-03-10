"use client";

import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { useToast } from "@/components/ui/use-toast";
import { ShieldAlert, ShieldCheck, ShieldX, Loader2 } from "lucide-react";

type TradingState = "ENABLED" | "DISABLED" | "EMERGENCY_FLATTEN";

export function KillSwitchControl() {
    const [tradingState, setTradingState] = useState<TradingState | null>(null);
    const [isLoading, setIsLoading] = useState(false);
    const { toast } = useToast();

    const fetchState = async () => {
        try {
            const res = await fetch("/api/v1/admin/trading/state");
            const data = await res.json();
            if (data.status === "success" && data.state) {
                setTradingState(data.state as TradingState);
            }
        } catch (error) {
            console.error("Failed to fetch trading state:", error);
        }
    };

    useEffect(() => {
        fetchState();
        // Poll every 10 seconds to keep dashboard state synced
        const interval = setInterval(fetchState, 10000);
        return () => clearInterval(interval);
    }, []);

    const handleAction = async (endpoint: string, expectedState: TradingState) => {
        setIsLoading(true);
        try {
            const res = await fetch(`/api/v1/admin/trading/${endpoint}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ trigger: "manual:admin_dashboard", reason: "User triggered from dashboard" }),
            });

            const data = await res.json();

            if (data.status === "success") {
                setTradingState(expectedState);
                toast({
                    title: "Success",
                    description: data.message,
                    variant: expectedState === "ENABLED" ? "default" : "destructive",
                });
            } else {
                toast({
                    title: "Error",
                    description: data.detail || "Action failed",
                    variant: "destructive",
                });
            }
        } catch (error) {
            console.error(`Failed to execute ${endpoint}:`, error);
            toast({
                title: "Error",
                description: "Network error occurred",
                variant: "destructive",
            });
        } finally {
            setIsLoading(false);
        }
    };

    if (tradingState === null) {
        return (
            <Card className="w-full h-full flex items-center justify-center min-h-[150px]">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </Card>
        );
    }

    return (
        <Card className={`border-2 transition-colors ${tradingState === "ENABLED" ? "border-green-500/50" :
                tradingState === "DISABLED" ? "border-yellow-500/50" : "border-red-500/50"
            }`}>
            <CardHeader className="pb-2">
                <div className="flex items-center justify-between">
                    <CardTitle className="flex items-center gap-2">
                        {tradingState === "ENABLED" && <ShieldCheck className="h-5 w-5 text-green-500" />}
                        {tradingState === "DISABLED" && <ShieldAlert className="h-5 w-5 text-yellow-500" />}
                        {tradingState === "EMERGENCY_FLATTEN" && <ShieldX className="h-5 w-5 text-red-500 animate-pulse" />}
                        Global Kill Switch
                    </CardTitle>
                    <div className="flex items-center gap-2">
                        <span className="text-sm font-medium">Status:</span>
                        <span className={`text-sm font-bold px-2 py-0.5 rounded-md ${tradingState === "ENABLED" ? "bg-green-500/10 text-green-500" :
                                tradingState === "DISABLED" ? "bg-yellow-500/10 text-yellow-500" :
                                    "bg-red-500/10 text-red-500 animate-pulse"
                            }`}>
                            {tradingState.replace("_", " ")}
                        </span>
                    </div>
                </div>
                <CardDescription>
                    Master control layer for all trading workers
                </CardDescription>
            </CardHeader>
            <CardContent>
                <div className="flex flex-col sm:flex-row gap-3 mt-4">
                    <Button
                        disabled={isLoading || tradingState === "ENABLED"}
                        onClick={() => handleAction("enable", "ENABLED")}
                        className="flex-1 bg-green-600 hover:bg-green-700 text-white"
                    >
                        {isLoading && tradingState !== "ENABLED" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                        Resume Trading
                    </Button>

                    <Button
                        disabled={isLoading || tradingState !== "ENABLED"}
                        onClick={() => handleAction("disable", "DISABLED")}
                        variant="outline"
                        className="flex-1 border-yellow-600 text-yellow-600 hover:bg-yellow-50 dark:hover:bg-yellow-900/20"
                    >
                        {isLoading && tradingState === "ENABLED" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                        Soft Stop (Halt New)
                    </Button>

                    <Button
                        disabled={isLoading || tradingState === "EMERGENCY_FLATTEN"}
                        onClick={() => handleAction("emergency-flatten", "EMERGENCY_FLATTEN")}
                        variant="destructive"
                        className="flex-1"
                    >
                        {isLoading && tradingState !== "EMERGENCY_FLATTEN" && tradingState !== "ENABLED" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                        EMERGENCY FLATTEN
                    </Button>
                </div>
            </CardContent>
        </Card>
    );
}
