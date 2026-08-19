-- diagnostic_all_params.lua
stopped = false
local output_file = "C:/Users/Public/MI_CODES/Qwen_coder/data/sber_all_params.txt"

function write_log(msg)
    local f = io.open(output_file, "a")
    if f then
        f:write(msg .. "\n")
        f:close()
    end
end

function OnInit()
    local f = io.open(output_file, "w")
    if f then f:close() end
    write_log("=== ALL SBER PARAMS ===")
end

function OnStop()
    stopped = true
end

local counter = 0

function main()
    while not stopped do
        counter = counter + 1
        
        if counter == 1 then
            -- Список всех возможных параметров Quik
            local all_params = {
                "LAST", "BID", "OFFER", "BIDDEPTH", "OFFERDEPTH",
                "VOLUME", "VALUE", "YIELD", "OPEN", "HIGH", "LOW",
                "PREVPRICE", "LASTCHANGE", "LASTCHANGEPRCNT", "NUMTRADES",
                "WAPRICE", "VOLTAGE", "BUYDEPO", "SELLDEPO",
                "LIMIT_UP", "LIMIT_DOWN", "UP_LIMIT", "DOWN_LIMIT",
                "MAXPRICE", "MINPRICE", "PRICEMIN", "PRICEMAX",
                "LOTVALUE", "LOTSIZE", "STEP", "SCALE",
                "SEC_FACE_VALUE", "SEC_FACE_UNIT", "SEC_MAT_DATE",
                "SEC_SHORT_NAME", "SEC_NAME", "SEC_CODE", "CLASS_CODE",
                "BIDDEPTHT", "OFFERDEPTHT", "DURATION",
                "TRADINGSTATUS", "SESSION_STATUS",
                "CHANGE", "CHANGEPRCNT", "TIME", "DATETIME",
                "BIDPRICE", "OFFERPRICE", "BIDQTY", "OFFERQTY",
                "NUMBIDS", "NUMOFFERS"
            }
            
            for _, param in ipairs(all_params) do
                local result = getParamEx("TQBR", "SBER", param)
                if result then
                    local val = tostring(result.param_value)
                    local img = tostring(result.param_image)
                    local typ = tostring(result.param_type)
                    if val ~= "0.000000" or img ~= "" then
                        write_log(string.format("%-20s val=%-15s img=%-15s type=%s", param, val, img, typ))
                    end
                end
            end
            
            -- Также проверим getQuoteLevel2
            write_log("\n=== QUOTE LEVEL 2 ===")
            local quote = getQuoteLevel2("TQBR", "SBER")
            if quote then
                write_log("bid_count = " .. tostring(quote.bid_count))
                write_log("offer_count = " .. tostring(quote.offer_count))
                if quote.bid_count > 0 then
                    write_log("bid[1].price = " .. tostring(quote.bid[1].price))
                    write_log("bid[1].quantity = " .. tostring(quote.bid[1].quantity))
                end
                if quote.offer_count > 0 then
                    write_log("offer[1].price = " .. tostring(quote.offer[1].price))
                    write_log("offer[1].quantity = " .. tostring(quote.offer[1].quantity))
                end
            else
                write_log("quote is nil")
            end
            
            stopped = true
        end
        
        sleep(100)
    end
end