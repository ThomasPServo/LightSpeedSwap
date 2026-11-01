#import "RadioProbe.h"
#import <objc/runtime.h>
#import <objc/message.h>
#import <dlfcn.h>

// Independently implemented from observed selector/field names. Do not add
// CoreTelephony setters or entitlements here. Private ABI changes fail closed.
static const char *Bare(const char *t) {
    while (*t && strchr("rnNoORV", *t)) ++t;
    return t;
}

static BOOL Signature(id object, NSString *name, NSUInteger count, const char *result) {
    SEL sel = NSSelectorFromString(name);
    if (!object || ![object respondsToSelector:sel]) return NO;
    NSMethodSignature *s = [object methodSignatureForSelector:sel];
    return s && s.numberOfArguments == count && strcmp(Bare(s.methodReturnType), result) == 0;
}

static id Getter(id object, NSString *name) {
    @try {
        if (!Signature(object, name, 2, "@")) return nil;
        return ((id (*)(id, SEL))objc_msgSend)(object, NSSelectorFromString(name));
    } @catch (NSException *exception) { return nil; }
}

static NSNumber *Scalar(id object, NSString *name) {
    @try {
        SEL selector = NSSelectorFromString(name);
        if (![object respondsToSelector:selector]) return nil;
        NSMethodSignature *s = [object methodSignatureForSelector:selector];
        if (!s || s.numberOfArguments != 2) return nil;
        const char *type = Bare(s.methodReturnType);
        if (!strcmp(type, "@")) {
            id value = Getter(object, name);
            return [value isKindOfClass:NSNumber.class] ? value : nil;
        }
        NSInvocation *inv = [NSInvocation invocationWithMethodSignature:s];
        inv.target = object; inv.selector = selector; [inv invoke];
        if (!strcmp(type, "B")) { BOOL n = NO; [inv getReturnValue:&n]; return @(n); }
        if (!strcmp(type, "c")) { char n = 0; [inv getReturnValue:&n]; return @(n); }
        if (!strcmp(type, "i")) { int n = 0; [inv getReturnValue:&n]; return @(n); }
        if (!strcmp(type, "I")) { unsigned n = 0; [inv getReturnValue:&n]; return @(n); }
        if (!strcmp(type, "q")) { long long n = 0; [inv getReturnValue:&n]; return @(n); }
        if (!strcmp(type, "Q")) { unsigned long long n = 0; [inv getReturnValue:&n]; return @(n); }
        return nil;
    } @catch (NSException *exception) { return nil; }
}

static NSString *Text(id value) {
    if (![value isKindOfClass:NSString.class]) return @"";
    NSString *s = value;
    if (s.length > 128) s = [s substringToIndex:128];
    NSRegularExpression *identifiers = [NSRegularExpression regularExpressionWithPattern:@"[0-9]{10,}|(?i)\\b[0-9a-f]{24,}\\b" options:0 error:NULL];
    return [identifiers stringByReplacingMatchesInString:s options:0 range:NSMakeRange(0, s.length) withTemplate:@"[redacted]"];
}

static NSDictionary *Failure(NSString *state, NSError *error) {
    NSMutableDictionary *d = [@{@"state":state} mutableCopy];
    if (error) {
        // No NSError.userInfo/description: they can contain subscriber data.
        d[@"error_domain"] = Text(error.domain); d[@"error_code"] = @(error.code);
    }
    return d;
}

static NSDictionary *ProjectConnection(id connection) {
    NSMutableArray *flows = [NSMutableArray array];
    id raw = Getter(connection, @"qosFlows");
    if ([raw isKindOfClass:NSArray.class]) {
        for (id flow in [(NSArray *)raw subarrayWithRange:NSMakeRange(0, MIN(64, [raw count]))]) {
            NSMutableDictionary *item = [NSMutableDictionary dictionary];
            for (NSString *name in @[@"flowId", @"bearerId"]) {
                NSNumber *n = Scalar(flow, name); if (n) item[name] = n;
            }
            for (NSString *name in @[@"txQos", @"rxQos"]) {
                NSNumber *n = Scalar(Getter(flow, name), @"qci");
                if (n) item[name] = @{@"qci":n};
            }
            if (item[@"txQos"] || item[@"rxQos"]) [flows addObject:item];
        }
    }
    return @{@"state":@"returned", @"apn":Text(Getter(connection, @"apnName")),
             @"interface":Text(Getter(connection, @"interfaceName")), @"flows":flows,
             @"connection_state":Scalar(connection, @"state") ?: NSNull.null,
             @"suspended":Scalar(connection, @"suspended") ?: NSNull.null};
}

static NSDictionary *ProjectData(id data) {
    NSMutableDictionary *out = [@{@"state":@"returned"} mutableCopy];
    for (NSString *key in @[@"newRadioSaDataBearer", @"newRadioNsaDataBearer", @"newRadioDataBearer", @"dataMode", @"attached"]) {
        NSNumber *n = Scalar(data, key); if (n) out[key] = n;
    }
    return out;
}

static NSDictionary *ProjectCells(id info, NSString *slot) {
    NSMutableArray *cells = [NSMutableArray array];
    id raw = Getter(info, @"legacyInfo");
    if ([raw isKindOfClass:NSArray.class]) {
        for (id cell in [(NSArray *)raw subarrayWithRange:NSMakeRange(0, MIN(64, [raw count]))]) {
            if (![cell isKindOfClass:NSDictionary.class]) continue;
            NSMutableDictionary *item = [NSMutableDictionary dictionary];
            // No cell ID, TAC, phone number, subscriber ID or UUID leaves here.
            for (NSString *suffix in @[@"CellRadioAccessTechnology", @"BandInfo", @"CellType", @"RSRP", @"RSRQ", @"SINR", @"SNR", @"NRARFCN", @"ARFCN", @"Bandwidth"]) {
                id value = cell[[@"kCTCellMonitor" stringByAppendingString:suffix]];
                if ([value isKindOfClass:NSNumber.class]) item[suffix] = value;
                else if ([value isKindOfClass:NSString.class]) item[suffix] = Text(value);
            }
            if (item.count) [cells addObject:item];
        }
    }
    return @{@"state":@"returned", @"slot":slot, @"cells":cells};
}

@interface RadioProbe ()
@property(nonatomic) dispatch_queue_t worker;
@property(nonatomic, strong) id client;
@property(nonatomic) BOOL busy;
@end

@implementation RadioProbe
- (instancetype)init {
    if ((self = [super init])) _worker = dispatch_queue_create("LightSpeedRadio.probe", DISPATCH_QUEUE_SERIAL);
    return self;
}

- (void)readWithCompletion:(void (^)(NSDictionary<NSString *,id> *))completion {
    @synchronized (self) {
        if (self.busy) { completion(@{@"state":@"busy"}); return; }
        self.busy = YES;
    }
    // A wedged synchronous XPC read cannot accumulate new worker threads.
    NSObject *gate = [NSObject new];
    __block BOOL finished = NO;
    void (^finish)(NSDictionary *) = ^(NSDictionary *result) {
        @synchronized(gate) {
            if (finished) return;
            finished = YES;
        }
        dispatch_async(dispatch_get_main_queue(), ^{ completion(result); });
    };
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 8 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{
        finish(@{@"state":@"timeout", @"detail":@"The device did not finish its read. No value was inferred."});
    });
    dispatch_async(self.worker, ^{
        NSMutableDictionary *report = [NSMutableDictionary dictionary];
        @try {
            static dispatch_once_t loadOnce;
            dispatch_once(&loadOnce, ^{
                dlopen("/System/Library/Frameworks/CoreTelephony.framework/CoreTelephony", RTLD_LAZY);
            });
            Class cls = NSClassFromString(@"CoreTelephonyClient");
            if (!cls) { report[@"state"] = @"unavailable"; }
            else {
                if (!self.client) self.client = [[cls alloc] init];
                report[@"state"] = @"completed";
                // Capture the current data-service descriptor on BOTH sides of
                // the reads. Identifiers stay in memory and are never exported.
                id before = [self syncRead:@"getCurrentDataServiceDescriptorSync:" error:NULL];
                NSString *beforeID = Getter(before, @"identifier");
                NSError *error = nil;
                id connection = [self syncRead:@"getInternetConnectionStateSync:" error:&error];
                report[@"qos"] = connection && !error ? ProjectConnection(connection) : Failure([self availability:@"getInternetConnectionStateSync:"], error);
                error = nil;
                id data = [self syncRead:@"getInternetDataStatusSync:" error:&error];
                report[@"data"] = data && !error ? ProjectData(data) : Failure([self availability:@"getInternetDataStatusSync:"], error);
                NSMutableArray *cellResults = [NSMutableArray array];
                // Contexts are obtained from the service. Never guess slot UUIDs
                // or send nil/broad contexts to circumvent access checks.
                error = nil;
                id active = [self syncRead:@"getActiveContexts:" error:&error];
                NSArray *subscriptions = Getter(active, @"subscriptions");
                if (error || ![subscriptions isKindOfClass:NSArray.class]) subscriptions = @[];
                for (id sub in [subscriptions subarrayWithRange:NSMakeRange(0, MIN(2, subscriptions.count))]) {
                    BOOL expired;
                    @synchronized(gate) { expired = finished; }
                    if (expired) break;
                    id uuid = Getter(sub, @"uuid");
                    Class contextClass = NSClassFromString(@"CTXPCServiceSubscriptionContext");
                    SEL factory = NSSelectorFromString(@"contextWithUUID:");
                    if (![uuid isKindOfClass:NSUUID.class] || !Signature(contextClass, @"contextWithUUID:", 3, "@")) continue;
                    const char *arg = Bare([[contextClass methodSignatureForSelector:factory] getArgumentTypeAtIndex:2]);
                    if (strcmp(arg, "@")) continue;
                    id context = ((id (*)(id, SEL, id))objc_msgSend)(contextClass, factory, uuid);
                    NSNumber *slot = Scalar(sub, @"slotID");
                    NSString *label = slot.intValue == 1 ? @"One" : slot.intValue == 2 ? @"Two" : @"Unknown";
                    [cellResults addObject:[self cells:context slot:label]];
                }
                report[@"cells"] = cellResults;
                report[@"contexts"] = active && !error ? @{@"state":@"returned", @"count":@(subscriptions.count)} : Failure([self availability:@"getActiveContexts:"], error);
                id after = [self syncRead:@"getCurrentDataServiceDescriptorSync:" error:NULL];
                NSString *afterID = Getter(after, @"identifier");
                BOOL stable = [beforeID isKindOfClass:NSString.class] && beforeID.length > 0 && [beforeID isEqual:afterID];
                report[@"stable_data_service"] = @(stable);
                NSNumber *instance = Scalar(after, @"instance");
                report[@"data_slot"] = stable && instance.intValue == 1 ? @"One" : stable && instance.intValue == 2 ? @"Two" : @"Unknown";
            }
        } @catch(NSException *exception) {
            report = [@{@"state":@"incompatible", @"detail":@"Private API shape changed; read stopped."} mutableCopy];
        }
        @synchronized(self) { self.busy = NO; }
        finish(report);
    });
}

- (NSString *)availability:(NSString *)name {
    return [self.client respondsToSelector:NSSelectorFromString(name)] ? @"unavailable_or_denied" : @"unsupported";
}

- (id)syncRead:(NSString *)name error:(NSError **)error {
    if (!Signature(self.client, name, 3, "@")) return nil;
    SEL sel = NSSelectorFromString(name);
    const char *arg = Bare([[self.client methodSignatureForSelector:sel] getArgumentTypeAtIndex:2]);
    if (strcmp(arg, "^@")) return nil;
    return ((id (*)(id, SEL, NSError **))objc_msgSend)(self.client, sel, error);
}

- (NSDictionary *)cells:(id)context slot:(NSString *)slot {
    NSString *name = @"copyCellInfo:completion:";
    if (!Signature(self.client, name, 4, "v")) return Failure(@"unsupported", nil);
    SEL sel = NSSelectorFromString(name);
    NSMethodSignature *s = [self.client methodSignatureForSelector:sel];
    if (strcmp(Bare([s getArgumentTypeAtIndex:2]), "@") || strcmp(Bare([s getArgumentTypeAtIndex:3]), "@?")) return Failure(@"incompatible", nil);
    dispatch_semaphore_t signal = dispatch_semaphore_create(0);
    NSObject *gate = [NSObject new];
    __block NSDictionary *result = nil;
    void (^callback)(id, NSError *) = ^(id value, NSError *error) {
        @synchronized(gate) { result = value && !error ? ProjectCells(value, slot) : Failure(@"unavailable_or_denied", error); }
        dispatch_semaphore_signal(signal);
    };
    ((void (*)(id, SEL, id, id))objc_msgSend)(self.client, sel, context, callback);
    if (dispatch_semaphore_wait(signal, dispatch_time(DISPATCH_TIME_NOW, 2 * NSEC_PER_SEC))) return Failure(@"timeout", nil);
    @synchronized(gate) { return result ?: Failure(@"empty", nil); }
}
@end
